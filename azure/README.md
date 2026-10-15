# Azure pipeline (preserved)

This folder holds the Azure version of the localization pipeline: the Docker local-inference generation measured in the end-semester report. It was moved here unchanged apart from folder paths and blanked secrets, so it can be re-run for side-by-side comparisons with [the AWS version](../aws/README.md).

| Path | What it is |
|---|---|
| `azure-pipelines.yml` | The three-stage Azure DevOps pipeline (extract → Docker translate → merge) |
| `API_based_HFace_AppInsight/` | Phase 1 (`phase1.py`) and Phase 3 (`phase3.py`), restored from git history, plus their input `source-folder/resx_file_10.resx` |
| `Docker-Local-Inference/` | Phase 2: Dockerfile and `translation.py` (NLLB-200 600M, CTranslate2 INT8), plus the quantization benchmark |
| `legacy-powershell/` | Archive of the earlier PowerShell generation. Not runnable: its `scripts/function1.ps1` and `function2.ps1` are not in the repo. |

## Running it again

1. In Azure DevOps, open the pipeline → **Edit** → **⋮** → **Settings**, and set **YAML file path** to `azure/azure-pipelines.yml`.
2. Make sure these pipeline variables exist (mark them secret): `AZURE_STORAGE_CONN`, `AZURE_SQL_CONN`, `HUGGINGFACE_TOKEN`, `APPINSIGHTS_CONNECTION_STRING`. The pipeline writes them into the `config.json` files at run time; the committed files contain empty values.
3. Run the pipeline manually (`trigger: none`).

## What changed compared with the report runs

- Folder paths gained an `azure/` prefix.
- The Phase 1 "Inject secrets" step now writes into `API_based_HFace_AppInsight/config.json`. It used to write into an unused root `config.json`, and Phase 1 only worked because its own config had keys hard-coded in it.
- No Python code changed.

Known issues kept on purpose, so the Azure behaviour matches the report:
- The "Commit & Push" step only sets the git identity; localized files are not committed.
- Phase 2 deletes a raw input even when translating it failed.
- `quantization_benchmark.py` forces `▁es` instead of `spa_Latn` as the target-language token.

**Security:** keys that were committed earlier are still in git history. Rotate the Azure storage key, the Azure SQL password and the Hugging Face tokens.
