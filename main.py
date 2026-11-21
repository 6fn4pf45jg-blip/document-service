from fastapi import FastAPI

from app.api.routes import router
from app.core.database import create_tables

app = FastAPI(title="Document Service")


@app.on_event("startup")
def startup():
    create_tables()


app.include_router(router)
