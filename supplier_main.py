#!/usr/bin/env python3
"""Supplier Database Access Feature — Flask entry point."""

import os

from flask import Flask, render_template

from supplier_db import db, suppliers_bp

app = Flask(__name__)

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------
_db_path = os.environ.get("SUPPLIER_DB_PATH", "suppliers.db")
app.config["SQLALCHEMY_DATABASE_URI"] = f"sqlite:///{_db_path}"
app.config["SQLALCHEMY_TRACK_MODIFICATIONS"] = False

# ---------------------------------------------------------------------------
# Extensions & blueprints
# ---------------------------------------------------------------------------
db.init_app(app)
app.register_blueprint(suppliers_bp)


@app.route("/suppliers")
def supplier_dashboard():
    return render_template("supplier_dashboard.html")


# ---------------------------------------------------------------------------
# Database initialisation
# ---------------------------------------------------------------------------
with app.app_context():
    db.create_all()

if __name__ == "__main__":
    app.run(debug=True, port=int(os.environ.get("PORT", 5001)))
