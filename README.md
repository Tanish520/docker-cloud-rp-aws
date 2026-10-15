# Cloud Localization Pipeline

Translates English `.resx` resource strings into other languages in three phases:

1. **Extract:** convert each `.resx` file into one XLIFF (`.xlf`) file per target language.
2. **Translate:** look each string up in a SQL translation memory and translate the misses with NLLB-200 (distilled 600M, CTranslate2 INT8) inside Docker; write new translations back to the memory.
3. **Merge:** turn the translated XLIFF files back into localized `.resx` files.

## Two implementations

| Folder | Cloud | Status |
|---|---|---|
| [azure/](azure/README.md) | Azure DevOps Pipelines, Blob Storage, Azure SQL, Application Insights | Preserved: the Docker generation measured in the end-semester report |
| [aws/](aws/README.md) | CodePipeline + CodeBuild, S3, RDS for SQL Server, CloudWatch | Current: used for future work |

Both use the same model, inference settings, workload and telemetry fields, so their results can be compared directly. The AWS design is in [the design spec](docs/superpowers/specs/2026-10-16-aws-migration-design.md).

## Shared inputs

[source-folder/](source-folder/) holds the 20 English `.resx` files. The comparison workload is `resx_file_10.resx` translated into `fr-FR`, `es-ES`, `hi-IN`, `de-DE`, `ja-JP`, `ru-RU` and `zh-CN`.

## Project documents

- [ENDSEM_REPORT_README.md](ENDSEM_REPORT_README.md): source material for the end-semester report.
- [idea.md](idea.md) and [implementation-checklist.md](implementation-checklist.md): the earlier optimization plan.

## Secrets

Never commit keys, passwords or tokens. Azure secrets are injected by the pipeline at run time; AWS uses IAM roles and Secrets Manager. Keys that were committed earlier remain in git history and must be rotated.
