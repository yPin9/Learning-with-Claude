from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict

from app.db import SessionLocal, init_db
from app.models import Task


@asynccontextmanager
async def lifespan(_app: FastAPI):
    init_db()
    yield


app = FastAPI(title="tasktrack", lifespan=lifespan)


class TaskCreate(BaseModel):
    title: str
    milestone: str | None = None


class TaskOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    title: str
    milestone: str | None
    done: bool


@app.post("/tasks", response_model=TaskOut)
def create_task(payload: TaskCreate) -> Task:
    with SessionLocal() as db:
        task = Task(title=payload.title, milestone=payload.milestone, done=False)
        db.add(task)
        db.commit()
        db.refresh(task)
        return task


@app.get("/tasks", response_model=list[TaskOut])
def list_tasks() -> list[Task]:
    with SessionLocal() as db:
        return list(db.query(Task).order_by(Task.id).all())


@app.patch("/tasks/{task_id}/complete", response_model=TaskOut)
def complete_task(task_id: int) -> Task:
    with SessionLocal() as db:
        task = db.get(Task, task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="task not found")
        task.done = True
        db.commit()
        db.refresh(task)
        return task
