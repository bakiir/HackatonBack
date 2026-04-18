import os
import logging
from datetime import timedelta
from dotenv import load_dotenv
from flask import Flask
from flask_cors import CORS
from flask_jwt_extended import JWTManager
from sqlalchemy.orm import sessionmaker
from create_db import engine

# Load environment variables
load_dotenv()

app = Flask(__name__)

# Config
app.config["JWT_SECRET_KEY"] = os.getenv("JWT_SECRET_KEY", "fallback-secret-key")
app.config["JWT_TOKEN_LOCATION"] = ["headers"]
app.config["JWT_ACCESS_TOKEN_EXPIRES"] = timedelta(minutes=30)

jwt = JWTManager(app)
CORS(app)

# Database Session
Session = sessionmaker(bind=engine)
session = Session()

# Import and Register Blueprints
from routes.auth_routes import auth_bp
from routes.classroom_routes import classroom_bp
from routes.proctor_routes import proctor_bp
from routes.session_routes import session_bp
from routes.schedule_routes import schedule_bp
from routes.exam_routes import exam_bp

app.register_blueprint(auth_bp)
app.register_blueprint(classroom_bp)
app.register_blueprint(proctor_bp)
app.register_blueprint(session_bp)
app.register_blueprint(schedule_bp)
app.register_blueprint(exam_bp)

if __name__ == '__main__':
    app.run(debug=True)
