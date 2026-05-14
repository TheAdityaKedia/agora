from fastapi import FastAPI

app = FastAPI(title="Agora")


@app.get("/events")
def list_events():
    raise NotImplementedError


@app.get("/events/{event_id}")
def get_event(event_id: str):
    raise NotImplementedError
