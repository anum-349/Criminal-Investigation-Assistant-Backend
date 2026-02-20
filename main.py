from fastapi import FastAPI
from database import Base, engine
from routers import auth, admin, investigator

Base.metadata.create_all(bind=engine)
app = FastAPI()

app.include_router(prefix="/api",router=auth.router)
app.include_router(prefix="/api",router=admin.router)
app.include_router(prefix="/api",router=investigator.router)