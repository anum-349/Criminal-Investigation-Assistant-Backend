from fastapi import FastAPI
from database import Base, engine
from routes import auth, admin, investigator
from fastapi.middleware.cors import CORSMiddleware

Base.metadata.create_all(bind=engine)

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        "*"
        # "http://localhost:5173",  # Vite React dev server
    ],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(prefix="/api",router=auth.router)
app.include_router(prefix="/api",router=admin.router)
app.include_router(prefix="/api",router=investigator.router)