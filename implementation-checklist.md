# Cloud Localization Pipeline - Implementation Checklist

**Quick Reference Guide for Performance Optimization**

---

## 🔴 **CRITICAL - Do Immediately**

- [ ] **Remove hardcoded credentials from `VM_MbartModel/config2.json`**
- [ ] **Add `config2.json` and `*.secret.json` to `.gitignore`**
- [ ] **Rotate all leaked credentials in Azure (Storage, SQL, VM IP)**
- [ ] **Move secrets to Azure DevOps pipeline variables or Azure Key Vault**

---

## 🚀 **Phase 1: Quick Wins (Week 1-2) - 30-40% Improvement**

### Pipeline Optimization
- [ ] Implement Azure DevOps pipeline caching for Python packages
- [ ] Add `Cache@2` task to all pipeline stages
- [ ] Set `PIP_CACHE_DIR` variable for pip caching

### XML Parsing
- [ ] Replace `xml.etree.ElementTree` with `lxml` in all Python files
- [ ] Add `lxml` to pipeline installation commands
- [ ] Update imports: `import lxml.etree as ET`

### Database Optimization
- [ ] Add composite index: `CREATE INDEX idx_translations_lookup ON Translations(TargetLang, SourceText)`
- [ ] Add source text index: `CREATE INDEX idx_translations_source ON Translations(SourceText)`
- [ ] Run `UPDATE STATISTICS Translations`

### Basic Parallelization
- [ ] Implement `ThreadPoolExecutor` in `phase1.py` for file processing
- [ ] Set `max_workers=32` for I/O-bound tasks
- [ ] Add progress tracking and error handling

### Metrics for VM Approach
- [ ] Copy `RunMetrics` class from API version to VM version
- [ ] Add metrics tracking to `VM_MbartModel/translation.py`
- [ ] Log metrics summary at end of each run
- [ ] (Optional) Add Azure Application Insights integration

---

## 🚀 **Phase 2: Major Optimizations (Week 3-5) - 60-70% Improvement**

### Full Parallelization
- [ ] Parallelize Phase 1 (Extract): Process multiple `.resx` files concurrently
- [ ] Parallelize Phase 2 (Translate): Process multiple `.xlf` blobs concurrently
- [ ] Parallelize Phase 3 (Load): Process multiple translated files concurrently
- [ ] Implement async blob operations with `azure.storage.blob.aio`

### SQL Bulk Operations
- [ ] Replace individual SQL queries with bulk `SELECT ... IN (...)` queries
- [ ] Implement `query_translations_bulk()` function
- [ ] Implement `insert_translations_bulk()` with `executemany()`
- [ ] Reduce N+1 query problem

### Redis Caching Layer
- [ ] Provision Azure Cache for Redis instance
- [ ] Implement `TranslationCache` class with Redis + SQL fallback
- [ ] Add cache hit/miss tracking to metrics
- [ ] Set TTL to 30 days for cached translations

### Adaptive Batching
- [ ] Create `AdaptiveBatcher` class with token-based batching
- [ ] Set `max_tokens=1024` and `max_items=50` for API approach
- [ ] Set `max_tokens=512` and `max_items=20` for VM approach
- [ ] Replace fixed batch sizes with dynamic batching

---

## 🚀 **Phase 3: Architecture Refactoring (Week 6-8) - Maintainability**

### Unified Codebase
- [ ] Create `src/` directory structure
- [ ] Move common code to shared modules (`config.py`, `utils/`, `cache/`)
- [ ] Create `translation/` module with base classes
- [ ] Implement translator factory pattern

### Configuration Management
- [ ] Create `config/config.base.json` with all settings
- [ ] Create `config/config.api.json` for API-specific overrides
- [ ] Create `config/config.vm.json` for VM-specific overrides
- [ ] Add `translation_engine` flag to switch between API/VM

### Code Organization
- [ ] Extract XML parsing logic to `utils/xml_parser.py`
- [ ] Extract blob storage logic to `utils/blob_storage.py`
- [ ] Create `BaseTranslator` abstract class
- [ ] Implement `HuggingFaceTranslator` and `VMTranslator` classes

### Testing
- [ ] Add unit tests for core functions
- [ ] Add integration tests for end-to-end pipeline
- [ ] Add performance benchmarks
- [ ] Set up CI/CD test automation

---

## 🚀 **Phase 4: Advanced Optimizations (Week 9-12) - 75-85% Improvement**

### VM Infrastructure Optimization
- [ ] Upgrade to GPU-enabled VM (Standard_NC4as_T4_v3 or better)
- [ ] Optimize FastAPI server with FP16 inference (`model.half()`)
- [ ] Implement model batching on VM side
- [ ] Add health check endpoint to VM API

### Auto-Scaling & Cost Optimization
- [ ] Implement VM auto-shutdown during off-hours (6 PM - 8 AM)
- [ ] Set up Azure Automation runbooks for start/stop
- [ ] (Optional) Configure VM Scale Sets for auto-scaling
- [ ] Set up cost alerts and budget monitoring

### Connection Pooling
- [ ] Implement `SQLConnectionPool` class
- [ ] Set pool size to 10 connections
- [ ] Add connection timeout and retry logic
- [ ] Monitor pool usage and adjust size

### Security Hardening
- [ ] Integrate Azure Key Vault for secret management
- [ ] Implement `SecureConfig` class with Key Vault client
- [ ] Use Managed Identity for authentication
- [ ] Remove all plaintext secrets from config files

### Monitoring & Observability
- [ ] Create Azure Monitor dashboard for pipeline metrics
- [ ] Set up alerts for pipeline failures (>5% error rate)
- [ ] Track key metrics: execution time, throughput, TM hit rate, cost
- [ ] Implement distributed tracing with Application Insights

---

## 📊 **Performance Targets**

| Metric | Current | Target | Status |
|--------|---------|--------|--------|
| Phase 1 Duration | 23 min | 1 min | ⬜ |
| Phase 2 Duration | 50 min | 8 min | ⬜ |
| Phase 3 Duration | 15 min | 2 min | ⬜ |
| **Total Pipeline** | **88 min** | **11 min** | ⬜ |
| Throughput | 680 seg/hr | 5,450 seg/hr | ⬜ |
| TM Hit Rate | Unknown | >70% | ⬜ |
| Cost per Segment | $0.003 | $0.0005 | ⬜ |

---

## 🔧 **Technology Stack Additions**

### Python Packages to Install
```bash
pip install lxml                    # Fast XML parsing
pip install redis                   # Redis caching
pip install aiohttp                 # Async HTTP requests
pip install azure-storage-blob-aio  # Async blob operations
pip install opencensus-ext-azure    # Application Insights
```

### Azure Resources to Provision
- [ ] Azure Cache for Redis (Basic tier for testing, Standard for production)
- [ ] Azure Key Vault (for secret management)
- [ ] GPU-enabled Azure VM (Standard_NC4as_T4_v3 recommended)
- [ ] (Optional) Azure Monitor dashboard
- [ ] (Optional) Application Insights instance

---

## 📝 **Code Changes Summary**

### Files to Modify
- [ ] `API_based_HFace_AppInsight/phase1.py` - Add parallelization
- [ ] `API_based_HFace_AppInsight/phase2.py` - Add async operations
- [ ] `API_based_HFace_AppInsight/phase3.py` - Add parallelization
- [ ] `API_based_HFace_AppInsight/translation.py` - Add bulk SQL, Redis cache
- [ ] `API_based_HFace_AppInsight/azure-pipelines.yml` - Add caching
- [ ] `VM_MbartModel/translation.py` - Add metrics, bulk SQL, Redis cache
- [ ] `VM_MbartModel/azure-pipelines.yml` - Add caching
- [ ] All Python files - Replace `xml.etree.ElementTree` with `lxml`

### Files to Create
- [ ] `src/config.py` - Unified configuration management
- [ ] `src/translation/base.py` - Abstract translator interface
- [ ] `src/translation/factory.py` - Translator factory
- [ ] `src/cache/redis_cache.py` - Redis caching layer
- [ ] `src/cache/sql_pool.py` - SQL connection pooling
- [ ] `src/metrics/tracker.py` - Unified metrics tracking
- [ ] `src/utils/xml_parser.py` - XML parsing utilities
- [ ] `src/utils/blob_storage.py` - Blob storage utilities
- [ ] `config/config.base.json` - Base configuration
- [ ] `tests/test_phase1.py` - Unit tests
- [ ] `tests/test_performance.py` - Performance benchmarks

### Files to Delete (After Refactoring)
- [ ] `VM_MbartModel/config2.json` - **DELETE IMMEDIATELY (security risk)**
- [ ] Duplicate code after unification

---

## 🎯 **Success Criteria**

### Performance
- ✅ Total pipeline execution time < 15 minutes
- ✅ Translation throughput > 5,000 segments/hour
- ✅ TM cache hit rate > 70%
- ✅ Pipeline success rate > 99.5%

### Cost
- ✅ Cost per segment < $0.001 (with VM approach)
- ✅ Monthly cost < $600 for 1M segments

### Security
- ✅ Zero hardcoded credentials in repository
- ✅ All secrets stored in Azure Key Vault or pipeline variables
- ✅ No security vulnerabilities in code

### Maintainability
- ✅ Single unified codebase (no duplication)
- ✅ Comprehensive test coverage (>80%)
- ✅ Full observability with metrics and dashboards

---

## 🔄 **Rollback Plan**

### If Issues Occur
1. Keep old code in `legacy/` branch
2. Use feature flags to toggle new optimizations
3. Monitor error rate - auto-rollback if >5%
4. Have Azure DevOps pipeline YAML for both old and new versions

### Gradual Rollout Strategy
1. **Week 1-2:** Test quick wins on dev environment
2. **Week 3:** Deploy to staging, run A/B tests
3. **Week 4:** Deploy to production with 10% traffic
4. **Week 5:** Increase to 50% traffic if metrics are good
5. **Week 6:** Full production rollout

---

## 📚 **Reference Documentation**

- **Detailed Implementation Guide:** `idea.md`
- **Azure DevOps Caching:** https://docs.microsoft.com/en-us/azure/devops/pipelines/release/caching
- **lxml Documentation:** https://lxml.de/
- **Azure Cache for Redis:** https://docs.microsoft.com/en-us/azure/azure-cache-for-redis/
- **Python asyncio:** https://docs.python.org/3/library/asyncio.html
- **Azure Key Vault:** https://docs.microsoft.com/en-us/azure/key-vault/

---

## 📞 **Support & Questions**

- Review detailed implementations in `idea.md`
- Check Azure DevOps pipeline logs for errors
- Monitor Azure Application Insights for performance metrics
- Review SQL query performance in Azure SQL Analytics

---

**Last Updated:** 2026-02-02  
**Version:** 1.0  
**Status:** Ready for Implementation
