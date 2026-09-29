"""Shared Flask extensions (SQLAlchemy, LoginManager, CSRF protection)."""
from __future__ import annotations

from flask_login import LoginManager
from flask_sqlalchemy import SQLAlchemy
from flask_wtf import CSRFProtect

db = SQLAlchemy()
login_manager = LoginManager()
csrf = CSRFProtect()

login_manager.login_view = "auth.login"
login_manager.login_message = "Bitte melde dich an, um fortzufahren."
login_manager.login_message_category = "info"
