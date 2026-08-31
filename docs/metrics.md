# RAG Evaluation Metrics

## Overview
Evaluation of the hybrid retrieval system (FTS + Vector + RRF fusion) against a golden dataset.

## Dataset
- **Questions**: 12 evaluation queries
- **Documents**: 17 indexed documents
- **Topics**: Vietnamese geography, AI/ML, business operations, technology, agent architecture

## Metrics

### Retrieval Performance

| Method | P@1 | P@3 | P@5 | Recall@5 | MRR |
|--------|-----|-----|-----|----------|-----|
| FTS      | 0.583 | 0.306 | 0.204 | 1.000 | 0.771 |
| VECTOR   | 0.667 | 0.250 | 0.167 | 0.833 | 0.757 |
| HYBRID   | 0.583 | 0.278 | 0.200 | 1.000 | 0.746 |

### Key Findings
- All methods perform similarly on this dataset

## How to Run
```bash
python scripts/run_rag_evaluation.py
```
