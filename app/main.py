from fastapi import FastAPI

app = FastAPI(title="AgentPay")


@app.get("/health")
def health():
    return {"status": "ok"}
