"""Check that per-pair reranking gives the same scores as the frozen padded batch."""
import os
import sys
import time

os.environ.setdefault("HF_HUB_OFFLINE", "1")
import pipeline as P  # noqa: E402  (sets sys.path for v2)
from langchain_chroma import Chroma
from langchain_huggingface import HuggingFaceEmbeddings
from sentence_transformers import CrossEncoder

query = sys.argv[1] if len(sys.argv) > 1 else "2025年合并口径下投资活动产生的现金流量净额是多少？"
emb = HuggingFaceEmbeddings(model_name="BAAI/bge-base-zh-v1.5", model_kwargs={"device": "cpu"},
                            encode_kwargs={"normalize_embeddings": True})
record = next(r for r in P.MANIFEST if r["company"] == "比亚迪")
store = Chroma(collection_name=record["collection_name"], persist_directory=str(P.V2 / record["vector_db_path"]),
               embedding_function=emb)
docs = store.similarity_search(query, k=20)
pairs = [[query, d.page_content] for d in docs]
model = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cpu")

t = time.perf_counter(); a = model.predict(pairs); ta = time.perf_counter() - t
t = time.perf_counter(); b = model.predict(pairs, batch_size=1, show_progress_bar=False); tb = time.perf_counter() - t
rank_a = sorted(range(20), key=lambda i: -a[i]); rank_b = sorted(range(20), key=lambda i: -b[i])
print(f"frozen batch: {ta:.1f}s   per-pair: {tb:.1f}s")
print("max |diff| =", float(abs(a - b).max()))
print("identical ranking:", rank_a == rank_b)
import torch
q = CrossEncoder("BAAI/bge-reranker-v2-m3", device="cpu")
import torch.ao.quantization as Q
Q.quantize_dynamic(q.model, {torch.nn.Linear}, dtype=torch.qint8, inplace=True)
t = time.perf_counter(); c = q.predict(pairs, batch_size=1, show_progress_bar=False); tc = time.perf_counter() - t
rank_c = sorted(range(20), key=lambda i: -c[i])
print(f"int8 dynamic: {tc:.1f}s  top5 same set: {set(rank_a[:5]) == set(rank_c[:5])}  top5 same order: {rank_a[:5] == rank_c[:5]}  full ranking same: {rank_a == rank_c}")
