from __future__ import annotations
from rag_manager.config import load_settings
from rag_manager.graph import build_workflow

def _load_workflow(): return build_workflow(load_settings())

if __name__ == "__main__":
    workflow=_load_workflow()
    while True:
        query=input("Bạn: ").strip()
        if query in {"exit","quit"}: break
        result=workflow.invoke({"query":query,"history":[],"tool_trace":[]})
        print("Lumi:",result.get("final_answer"))
