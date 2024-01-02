# COVID-19 Data Engineering Project

## 1. Introduction

This project demonstrates an end-to-end data engineering solution for handling COVID-19 data. The pipeline includes data ingestion using Azure Data Factory, storage in Azure Data Lake Gen2, transformation with Azure Databricks, modeling in Azure Synapse, and visualization using Power BI.

## 2. Project Planning and Definition

### Data Flow

1. **Data Ingestion (Azure Data Factory):**
   - Collects COVID-19 data from various sources.
   - Utilizes data connectors and scheduling capabilities for automated ingestion.

2. **Data Storage (Azure Data Lake Gen2):**
   - Stores processed COVID-19 data securely and cost-effectively.
   - Maintains organized data in a specific container named "bronze-data."

3. **Data Transformation (Azure Databricks):**
   - Uses Databricks clusters for data cleansing, normalization, and feature engineering.
   - Securely connects Azure Databricks with Azure Storage accounts.

4. **Data Modeling (Azure Synapse):**
   - Utilizes Azure Synapse for building data models and performing complex analytical tasks.

5. **Data Visualization (Power BI):**
   - Power BI is employed to create interactive dashboards and reports for stakeholders.

## 3. Architectural Overview

This project employs Azure services to efficiently process and analyze COVID-19 data. The architecture ensures efficiency, security, and scalability in handling epidemic data analysis.

## 4. Data Collection and Ingestion

### Raw Data
- Data is extracted from GitHub, containing information about COVID-19 victims and cases.
  
### Data Ingestion Process
- Utilizes Azure Data Factory for seamless ingestion.
- Configures source dataset as an HTTP source and destination as the "raw-data" container.
- Files are copied with minor modifications to enhance clarity.

### Data Storage
- Organizes data in Azure Data Lake Storage within the "bronze-data" container.

## 5. Data Transformation

### Data Transformation Phase
- Uses Azure Databricks with notebook environment for Spark code execution.
- Establishes a secure connection between Azure Databricks and Azure Storage accounts.
- Spark code processes and transforms COVID-19 data for analysis.

## 6. Data Modeling

### Loading into Azure Synapse
- Copies transformed data into Azure Synapse Analytics.
- Creates external tables in the Lake Database named "CovidDB."

### Data Relationships
- Establishes relationships between dimension and fact tables.

## 7. Data Visualization

### Total Daily Hospital Admissions per Country Over Time
- Queries the total daily hospital admissions per country over time.
- Uses Power BI's "Play Axis" for a time series animation.

### Trends Over Time
- Utilizes a line chart to visualize trends in daily hospital admissions over time.

### Variations Between Countries
- Presents variations between countries using a Bar Chart visualization.

### Seasonal Patterns
- Identifies seasonal patterns through a seven-day moving average.

## Note:
This README provides an overview of the project, covering data flow, architectural design, and specific phases such as data ingestion, storage, transformation, modeling, and visualization. Detailed code snippets and configurations are provided for each phase. The visualization section emphasizes dynamic presentations using the "Play Axis" feature in Power BI for enhanced data exploration.
