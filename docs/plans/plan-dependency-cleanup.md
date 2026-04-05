# Plan: Dependency Cleanup — Split requirements.txt into tiers (#1792)

## Open Questions

- **kafka-python (4 files)**: Used in `server/main.py`, `dispatch_service.py`, `audit_log.py`, `dead_letter_queue.py`. Can these be migrated to aiokafka (15 files already use it)? If so, kafka-python can be removed entirely. If not, keep it.

## Phase 1: Remove confirmed-unused packages + split into tiers

### Affected Files

- `requirements.txt` — remove Flask, confluent-kafka, libvirt-python; mark as base
- `requirements-ml.txt` — create with PyTorch, transformers, gymnasium, stable-baselines3 (new)
- `requirements-blockchain.txt` — create with web3, eth-* packages (new)
- `Dockerfile` — update to conditionally install tiered requirements

### Changes

**Remove from `requirements.txt`**:
- `Flask==3.1.2` (line 109) — 6 files, all tests or SFE test_generation, not production server
- `flask-cors==6.0.1` (line 110)
- `Flask-JWT-Extended==4.7.1` (line 111)
- `Flask-Limiter==4.0.0` (line 112)
- `flask-swagger-ui==5.21.0` (line 113)
- `confluent-kafka==2.3.0` (line 184) — only 2 files, both have graceful degradation
- `libvirt-python>=10.0.0` (line 429) — only 2 files, already in requirements-optional.txt

**Create `requirements-ml.txt`** — move these FROM requirements.txt:
- `torch==2.9.1`, `torchaudio==2.9.1`, `torchvision==0.24.1`
- `transformers`, `sentence-transformers`
- `stable-baselines3`, `gymnasium`
- `scikit-learn`, `faiss-cpu`

**Create `requirements-blockchain.txt`** — move these:
- `web3==7.14.0`
- `eth-account`, `eth-abi`, `eth-utils`, `eth-typing`, `eth-hash`, `rlp`

**Update `Dockerfile`**:
```dockerfile
# Base dependencies
COPY requirements.txt .
RUN pip install -r requirements.txt

# Optional: ML dependencies
COPY requirements-ml.txt .
RUN pip install -r requirements-ml.txt || echo "ML deps skipped"

# Optional: Blockchain dependencies
COPY requirements-blockchain.txt .
RUN pip install -r requirements-blockchain.txt || echo "Blockchain deps skipped"
```

### Unit Tests

No new tests — verify existing imports have graceful degradation:
- Flask imports in test files use `try/except ImportError`
- confluent-kafka imports use `try/except`
- libvirt imports are gated by `ENABLE_LIBVIRT` env var

### CI Validation

```bash
pip install -r requirements.txt  # Should succeed without ML/blockchain/Flask
```

---

## Phase 2: Clean up redundant requirements files

### Affected Files

- `requirements-no-libvirt.txt` — delete (replaced by removing libvirt from base)
- `requirements-optional.txt` — merge unique entries into appropriate tier files

### Changes

**Delete `requirements-no-libvirt.txt`** — it was a workaround for the libvirt system dependency. With libvirt moved to optional, the base `requirements.txt` is now equivalent.

**Merge `requirements-optional.txt`** unique packages into tier files or keep as-is for HSM/Fabric support.

### CI Validation

```bash
pip install -r requirements.txt
pip install -r requirements-ml.txt
pip install -r requirements-blockchain.txt
```

---

## Summary

| Metric | Before | After |
|--------|--------|-------|
| Base requirements | 442 lines | ~380 lines (-62) |
| Flask packages | 5 in base | 0 (removed) |
| Kafka clients | 3 | 2 (confluent-kafka removed) |
| Tier files | 1 main + 4 variants | 1 base + 2 tier + 1 optional |
| Install size (base) | ~4GB+ | ~2GB (no PyTorch/torch*) |
