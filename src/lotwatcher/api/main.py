from fastapi import FastAPI

app = FastAPI(title="LotWatcher")


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}
