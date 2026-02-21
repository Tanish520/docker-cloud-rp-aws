# How to Run

This project has two implementations that share the same three phases:
- Phase 1: Extract .resx to XLIFF and upload to Azure Blob Storage
- Phase 2: Translate (SQL Translation Memory + model) and upload translated XLIFF
- Phase 3: Merge translated XLIFF back into localized .resx

You can run locally or in Azure DevOps Pipelines.

## 1) Choose a variant

- API-based (HuggingFace Inference API):
  - Folder: API_based_HFace_AppInsight
  - Config: API_based_HFace_AppInsight/config.json

- VM-based (VM-hosted mBART via FastAPI):
  - Folder: VM_MbartModel
  - Config: VM_MbartModel/config.json

## 2) Prerequisites

### Common
- Python 3.x
- Azure Blob Storage account + connection string
- Azure SQL Database + connection string (Translation Memory table required)

### Python packages
API-based:
- azure-storage-blob
- requests
- pyodbc
- opencensus-ext-azure

VM-based:
- azure-storage-blob
- requests
- pyodbc

### ODBC driver (macOS)
`pyodbc` requires the Microsoft ODBC driver for SQL Server to be installed on your Mac.

## 3) Configure config.json

Update the placeholders in the config for your chosen variant.

API-based required keys:
- blob_connection_string
- sql_conn_str
- hf_api_token

API-based optional:
- app_insights_connection_string

VM-based required keys:
- blob_connection_string
- sql_conn_str
- hf_api_token
- vm_ip
- vm_port

Also set paths and containers as needed:
- SourceRepoPath (default: source-folder)
- TargetResxPath (default: target-folder)
- raw_container (default: raw-files)
- translated_container (default: trans-files)
- TargetLanguages

## 4) Ensure Translation Memory table exists

The translation scripts expect a table named `Translations` with columns:
- SourceText
- TargetLang
- TranslatedText

## 5) Run locally

From the chosen variant folder:

```bash
python3 phase1.py config.json
python3 translation.py config.json
python3 phase3.py config.json
```

Outputs are written to:
- Azure Blob Storage containers (raw-files, trans-files)
- Localized .resx files under TargetResxPath

## 6) Run in Azure DevOps Pipelines

Each variant includes an Azure DevOps pipeline file:
- API-based: API_based_HFace_AppInsight/azure-pipelines.yml
- VM-based: VM_MbartModel/azure-pipelines.yml

Set the following pipeline secrets/variables:
- AZURE_STORAGE_CONN
- AZURE_SQL_CONN
- HUGGINGFACE_TOKEN
- (API-based only) APPINSIGHTS_CONNECTION_STRING
- (VM-based only) VM_IP, VM_PORT

The pipeline runs the same three phases and can commit localized .resx files back to the repo.

## 7) Notes

- The VM-based variant assumes a FastAPI translation server is running and reachable at vm_ip:vm_port.
- If no translated XLIFF files are present, Phase 3 will exit early.
- Blob containers are created if they do not exist.

# Introduction
This project provides a solution for localizing `.resx` files using a three-phase process: extraction to XLIFF, translation (via SQL Translation Memory and a language model), and merging translated XLIFF back into `.resx`. It supports both API-based (HuggingFace Inference API) and VM-based (VM-hosted mBART via FastAPI) translation variants.

# Getting Started
Refer to the "How to Run" section above for detailed instructions on setting up prerequisites, configuring `config.json`, and running the project locally or in Azure DevOps Pipelines.

# Build and Test
The project is designed to be run via Python scripts as described in the "Run Locally" section. Testing involves verifying the correct extraction, translation, and merging of `.resx` files.

# Contribute
Contributions are welcome! Please follow the existing code structure and submit pull requests for any improvements or bug fixes.