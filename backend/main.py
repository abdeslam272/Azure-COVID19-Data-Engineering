import os
import io
import re
import json
import time
import uuid
import asyncio
from datetime import datetime
from typing import List, Optional, Dict, Any
from concurrent.futures import ThreadPoolExecutor

from fastapi import FastAPI, UploadFile, File, Form, HTTPException
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from motor.motor_asyncio import AsyncIOMotorClient
from dotenv import load_dotenv
from PyPDF2 import PdfReader
from docx import Document
from google import genai

load_dotenv()

MONGODB_URL = os.getenv("MONGODB_URL", "mongodb://localhost:27017")
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
MAX_WORKERS = int(os.getenv("MAX_WORKERS", "24"))
DEMO_MODE = os.getenv("DEMO_MODE", "false").lower() == "true"

app = FastAPI(title="LLM Resume Screener", version="1.0.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

mongo_client = AsyncIOMotorClient(MONGODB_URL)
db = mongo_client["resume_screener"]
jobs_collection = db["jobs"]
resumes_collection = db["resumes"]
results_collection = db["results"]

executor = ThreadPoolExecutor(max_workers=MAX_WORKERS)

genai_client = genai.Client(api_key=GEMINI_API_KEY) if GEMINI_API_KEY and not DEMO_MODE else None


class ScreenRequest(BaseModel):
    job_id: str


class JDTextRequest(BaseModel):
    job_description: str
    title: Optional[str] = "Untitled Role"


SAMPLE_JD = """
Senior Python Backend Engineer
Requirements:
- 4+ years Python development
- FastAPI or Django REST experience
- Experience with MongoDB/PostgreSQL
- Cloud deployment knowledge
- REST API design, async programming, Docker
- Good communication and debugging skills
Preferred:
- LLM / AI product experience
- React basics
""".strip()


SAMPLE_RESUMES = [
    {
        "candidate_name": "Aarav Sharma",
        "source_name": "aarav_sharma_resume.pdf",
        "resume_text": "Python backend engineer with 5 years experience building FastAPI services, MongoDB APIs, Docker deployments, async systems, and recruiter tooling. Worked on LLM resume parsers and internal AI automation.",
    },
    {
        "candidate_name": "Priya Reddy",
        "source_name": "priya_reddy_resume.pdf",
        "resume_text": "Software engineer with 3 years experience in Django, PostgreSQL, REST APIs, AWS deployment, and frontend collaboration. Built analytics dashboards and automation tools in Python.",
    },
    {
        "candidate_name": "Rohan Verma",
        "source_name": "rohan_verma_resume.pdf",
        "resume_text": "Frontend-heavy engineer using React and TypeScript. Some Node.js backend exposure, limited Python API work, strong UI focus, no strong FastAPI or database depth.",
    },
]


def now_utc() -> str:
    return datetime.utcnow().isoformat()


@app.on_event("startup")
async def startup_indexes() -> None:
    await jobs_collection.create_index("job_id", unique=True)
    await resumes_collection.create_index([("job_id", 1), ("resume_id", 1)], unique=True)
    await results_collection.create_index("job_id", unique=True)


@app.get("/")
async def root() -> Dict[str, Any]:
    return {
        "message": "LLM Resume Screener API is running",
        "demo_mode": DEMO_MODE,
        "time": now_utc(),
    }


@app.post("/seed-demo")
async def seed_demo() -> Dict[str, Any]:
    job_id = str(uuid.uuid4())
    await jobs_collection.insert_one(
        {
            "job_id": job_id,
            "title": "Senior Python Backend Engineer",
            "job_description": SAMPLE_JD,
            "created_at": now_utc(),
            "status": "ready",
        }
    )

    for item in SAMPLE_RESUMES:
        await resumes_collection.insert_one(
            {
                "resume_id": str(uuid.uuid4()),
                "job_id": job_id,
                "candidate_name": item["candidate_name"],
                "source_name": item["source_name"],
                "resume_text": item["resume_text"],
                "created_at": now_utc(),
            }
        )

    return {"job_id": job_id, "message": "Demo job and resumes inserted"}


@app.post("/upload-jd")
async def upload_jd(
    job_description: Optional[str] = Form(default=None),
    title: Optional[str] = Form(default="Untitled Role"),
    file: Optional[UploadFile] = File(default=None),
):
    text = ""

    if job_description and job_description.strip():
        text = job_description.strip()
    elif file:
        text = await extract_text_from_upload(file)
    else:
        raise HTTPException(status_code=400, detail="Provide job_description text or a file")

    if not text.strip():
        raise HTTPException(status_code=400, detail="Job description is empty")

    job_id = str(uuid.uuid4())
    await jobs_collection.insert_one(
        {
            "job_id": job_id,
            "title": title or "Untitled Role",
            "job_description": text,
            "created_at": now_utc(),
            "status": "jd_uploaded",
        }
    )

    return {"job_id": job_id, "title": title, "message": "Job description uploaded"}


@app.post("/upload-jd-json")
async def upload_jd_json(payload: JDTextRequest):
    if not payload.job_description.strip():
        raise HTTPException(status_code=400, detail="Job description is empty")

    job_id = str(uuid.uuid4())
    await jobs_collection.insert_one(
        {
            "job_id": job_id,
            "title": payload.title,
            "job_description": payload.job_description,
            "created_at": now_utc(),
            "status": "jd_uploaded",
        }
    )

    return {"job_id": job_id, "title": payload.title, "message": "Job description uploaded"}


@app.post("/upload-resumes")
async def upload_resumes(job_id: str = Form(...), files: List[UploadFile] = File(...)):
    if len(files) > 500:
        raise HTTPException(status_code=400, detail="Maximum 500 resumes allowed per batch")

    job = await jobs_collection.find_one({"job_id": job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    inserted = 0
    for file in files:
        text = await extract_text_from_upload(file)
        candidate_name = infer_candidate_name(text, file.filename or "Unknown")
        await resumes_collection.insert_one(
            {
                "resume_id": str(uuid.uuid4()),
                "job_id": job_id,
                "candidate_name": candidate_name,
                "source_name": file.filename or "uploaded_resume",
                "resume_text": text,
                "created_at": now_utc(),
            }
        )
        inserted += 1

    await jobs_collection.update_one({"job_id": job_id}, {"$set": {"status": "resumes_uploaded"}})
    return {"job_id": job_id, "uploaded_count": inserted}


@app.post("/screen")
async def screen_resumes(payload: ScreenRequest):
    job = await jobs_collection.find_one({"job_id": payload.job_id})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")

    resumes = await resumes_collection.find({"job_id": payload.job_id}).to_list(length=500)
    if not resumes:
        raise HTTPException(status_code=400, detail="No resumes found for this job")

    await jobs_collection.update_one(
        {"job_id": payload.job_id},
        {"$set": {"status": "screening", "screening_started_at": now_utc()}},
    )

    started = time.perf_counter()
    results = await run_screening_pipeline(job, resumes)
    elapsed = round(time.perf_counter() - started, 2)

    ranked = sorted(results, key=lambda x: x["overall_score"], reverse=True)
    top_candidate = ranked[0]["candidate_name"] if ranked else None
    avg_score = round(sum(r["overall_score"] for r in ranked) / len(ranked), 2) if ranked else 0

    response_doc = {
        "job_id": payload.job_id,
        "title": job.get("title", "Untitled Role"),
        "status": "completed",
        "processed_count": len(ranked),
        "avg_score": avg_score,
        "time_taken_seconds": elapsed,
        "top_candidate": top_candidate,
        "results": ranked,
        "updated_at": now_utc(),
    }

    await results_collection.update_one(
        {"job_id": payload.job_id},
        {"$set": response_doc},
        upsert=True,
    )

    await jobs_collection.update_one(
        {"job_id": payload.job_id},
        {"$set": {"status": "completed", "screening_completed_at": now_utc()}},
    )

    return response_doc


@app.get("/results/{job_id}")
async def get_results(job_id: str):
    result = await results_collection.find_one({"job_id": job_id}, {"_id": 0})
    if not result:
        raise HTTPException(status_code=404, detail="Results not found")
    return result


@app.get("/job/{job_id}")
async def get_job(job_id: str):
    job = await jobs_collection.find_one({"job_id": job_id}, {"_id": 0})
    if not job:
        raise HTTPException(status_code=404, detail="Job not found")
    return job


async def extract_text_from_upload(file: UploadFile) -> str:
    filename = (file.filename or "").lower()
    content = await file.read()

    if filename.endswith(".pdf"):
        return await asyncio.get_event_loop().run_in_executor(executor, extract_pdf_text_from_bytes, content)
    if filename.endswith(".docx"):
        return await asyncio.get_event_loop().run_in_executor(executor, extract_docx_text_from_bytes, content)
    if filename.endswith(".txt"):
        return content.decode("utf-8", errors="ignore")

    raise HTTPException(status_code=400, detail=f"Unsupported file type: {filename}")


def extract_pdf_text_from_bytes(content: bytes) -> str:
    reader = PdfReader(io.BytesIO(content))
    pages = []
    for page in reader.pages:
        pages.append(page.extract_text() or "")
    return "\n".join(pages).strip()


def extract_docx_text_from_bytes(content: bytes) -> str:
    doc = Document(io.BytesIO(content))
    return "\n".join([p.text for p in doc.paragraphs]).strip()


def infer_candidate_name(text: str, fallback_filename: str) -> str:
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines:
        first = lines[0]
        if 2 <= len(first.split()) <= 4 and len(first) < 50:
            return first

    name = os.path.splitext(os.path.basename(fallback_filename))[0]
    name = re.sub(r"[_\-.]+", " ", name).strip().title()
    return name or "Unknown Candidate"


async def run_screening_pipeline(job: Dict[str, Any], resumes: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    loop = asyncio.get_event_loop()
    tasks = [loop.run_in_executor(executor, score_resume_sync, job, resume) for resume in resumes]
    return await asyncio.gather(*tasks)


def score_resume_sync(job: Dict[str, Any], resume: Dict[str, Any]) -> Dict[str, Any]:
    jd_text = job.get("job_description", "")
    resume_text = resume.get("resume_text", "")

    if genai_client:
        try:
            return gemini_score_resume(job, resume, jd_text, resume_text)
        except Exception:
            return heuristic_score_resume(job, resume, jd_text, resume_text, fallback_reason="Gemini call failed, used heuristic fallback")

    return heuristic_score_resume(job, resume, jd_text, resume_text, fallback_reason="Demo mode heuristic scoring")


def gemini_score_resume(job: Dict[str, Any], resume: Dict[str, Any], jd_text: str, resume_text: str) -> Dict[str, Any]:
    prompt = f"""
You are an enterprise recruiter copilot scoring one resume against one job description.
Return ONLY valid JSON.

Scoring rubric:
- skills_match: 0-100
- experience_relevance: 0-100
- education_fit: 0-100
- overall_score: 0-100
- matched_skills: list of strings
- key_skill_tags: list of strings
- red_flags: list of strings
- reasoning: 3-6 concise bullet-like sentences in a single string

Job Title: {job.get('title', 'Untitled Role')}

JOB DESCRIPTION:
{jd_text}

RESUME:
{resume_text}

JSON schema:
{{
  "skills_match": 0,
  "experience_relevance": 0,
  "education_fit": 0,
  "overall_score": 0,
  "matched_skills": ["Python", "FastAPI"],
  "key_skill_tags": ["backend", "api", "mongodb"],
  "red_flags": ["Limited cloud exposure"],
  "reasoning": "Strong backend match..."
}}
""".strip()

    response = genai_client.models.generate_content(
        model=GEMINI_MODEL,
        contents=prompt,
    )
    text = (response.text or "").strip()
    parsed = safe_json_parse(text)
    if not parsed:
        return heuristic_score_resume(job, resume, jd_text, resume_text, fallback_reason="Invalid Gemini JSON, used heuristic fallback")

    return normalize_result(job, resume, parsed, model_name=GEMINI_MODEL)


def heuristic_score_resume(job: Dict[str, Any], resume: Dict[str, Any], jd_text: str, resume_text: str, fallback_reason: str) -> Dict[str, Any]:
    jd_skills = extract_skill_keywords(jd_text)
    resume_skills = extract_skill_keywords(resume_text)
    matched = sorted(list(jd_skills.intersection(resume_skills)))

    skills_match = min(100, int((len(matched) / max(len(jd_skills), 1)) * 100))

    experience_years = extract_experience_years(resume_text)
    jd_years = extract_required_years(jd_text)
    if jd_years == 0:
        experience_relevance = min(100, 50 + experience_years * 8)
    else:
        ratio = experience_years / max(jd_years, 1)
        experience_relevance = max(20, min(100, int(ratio * 100)))

    education_fit = 78
    if any(token in resume_text.lower() for token in ["b.tech", "bachelor", "master", "m.tech", "b.e", "degree"]):
        education_fit = 88
    if "phd" in resume_text.lower():
        education_fit = 92

    overall_score = int(round((skills_match * 0.45) + (experience_relevance * 0.35) + (education_fit * 0.20)))

    red_flags = []
    if experience_years < jd_years:
        red_flags.append("Experience below stated requirement")
    if "fastapi" in jd_text.lower() and "fastapi" not in resume_text.lower():
        red_flags.append("No direct FastAPI evidence")
    if "mongodb" in jd_text.lower() and "mongodb" not in resume_text.lower() and "postgresql" not in resume_text.lower():
        red_flags.append("Database match appears limited")

    reasoning = (
        f"Matched {len(matched)} core skills against the job description. "
        f"Estimated experience: {experience_years} years versus requirement of {jd_years} years. "
        f"Overall fit is driven by backend/Python alignment, skill overlap, and education evidence. "
        f"Scoring source: {fallback_reason}."
    )

    parsed = {
        "skills_match": skills_match,
        "experience_relevance": experience_relevance,
        "education_fit": education_fit,
        "overall_score": overall_score,
        "matched_skills": matched[:20],
        "key_skill_tags": sorted(list(resume_skills))[:12],
        "red_flags": red_flags[:8],
        "reasoning": reasoning,
    }
    return normalize_result(job, resume, parsed, model_name="heuristic")


SKILL_VOCAB = {
    "python", "fastapi", "django", "flask", "react", "mongodb", "postgresql", "mysql", "docker", "kubernetes",
    "aws", "gcp", "azure", "rest", "api", "asyncio", "redis", "llm", "ai", "machine learning", "git",
    "javascript", "typescript", "html", "css", "sql", "pandas", "numpy", "langchain", "rag", "debugging",
    "communication", "microservices", "linux", "ci/cd"
}


def extract_skill_keywords(text: str) -> set:
    lower = text.lower()
    found = set()
    for skill in SKILL_VOCAB:
        if skill in lower:
            found.add(skill)
    return found


def extract_experience_years(text: str) -> int:
    patterns = [
        r"(\d+)\+?\s+years",
        r"experience of\s+(\d+)\+?\s+years",
        r"(\d+)\+?\s+yrs",
    ]
    lower = text.lower()
    for pattern in patterns:
        match = re.search(pattern, lower)
        if match:
            return int(match.group(1))
    return 2


def extract_required_years(text: str) -> int:
    lower = text.lower()
    match = re.search(r"(\d+)\+?\s+years", lower)
    if match:
        return int(match.group(1))
    return 0


def safe_json_parse(text: str) -> Optional[Dict[str, Any]]:
    try:
        return json.loads(text)
    except Exception:
        pass

    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        chunk = text[start:end + 1]
        try:
            return json.loads(chunk)
        except Exception:
            return None
    return None


def clamp_score(value: Any) -> int:
    try:
        num = int(float(value))
    except Exception:
        num = 0
    return max(0, min(100, num))


def normalize_result(job: Dict[str, Any], resume: Dict[str, Any], parsed: Dict[str, Any], model_name: str) -> Dict[str, Any]:
    return {
        "resume_id": resume.get("resume_id"),
        "candidate_name": resume.get("candidate_name", "Unknown Candidate"),
        "source_name": resume.get("source_name", "resume.pdf"),
        "skills_match": clamp_score(parsed.get("skills_match", 0)),
        "experience_relevance": clamp_score(parsed.get("experience_relevance", 0)),
        "education_fit": clamp_score(parsed.get("education_fit", 0)),
        "overall_score": clamp_score(parsed.get("overall_score", 0)),
        "matched_skills": parsed.get("matched_skills", [])[:20],
        "key_skill_tags": parsed.get("key_skill_tags", [])[:20],
        "red_flags": parsed.get("red_flags", [])[:10],
        "reasoning": str(parsed.get("reasoning", "No reasoning provided")).strip(),
        "model_used": model_name,
        "screened_at": now_utc(),
    }


@app.exception_handler(HTTPException)
async def http_exception_handler(_, exc: HTTPException):
    return JSONResponse(
        status_code=exc.status_code,
        content={"detail": exc.detail},
    )


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_, exc: RequestValidationError):
    return JSONResponse(
        status_code=422,
        content={"detail": exc.errors()},
    )


@app.exception_handler(Exception)
async def global_exception_handler(_, exc: Exception):
    return JSONResponse(
        status_code=500,
        content={"detail": f"Internal server error: {str(exc)}"},
    )