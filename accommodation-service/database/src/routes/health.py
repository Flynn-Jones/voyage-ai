from flask import Blueprint, jsonify
from sqlalchemy import text
from db import SessionLocal

bp = Blueprint("health", __name__)


@bp.get("/health")
def health_check():
    db_status = "healthy"
    try:
        with SessionLocal() as session:
            # Execute a lightweight ping query
            session.execute(text("SELECT 1"))
    except Exception as e:
        db_status = f"unhealthy: {str(e)}"
        return (
            jsonify(
                {
                    "status": "unhealthy",
                    "database": db_status,
                }
            ),
            503,
        )

    return (
        jsonify(
            {
                "status": "healthy",
                "database": db_status,
            }
        ),
        200,
    )
