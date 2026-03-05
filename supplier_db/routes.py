"""REST API routes for the Supplier Database Access Feature."""

from flask import Blueprint, jsonify, request

from .models import Supplier, db

suppliers_bp = Blueprint("suppliers", __name__, url_prefix="/api/suppliers")

_ALLOWED_STATUSES = {"active", "inactive"}


def _supplier_or_404(supplier_id: int):
    supplier = db.session.get(Supplier, supplier_id)
    if supplier is None:
        return None, (jsonify({"error": f"Supplier {supplier_id} not found"}), 404)
    return supplier, None


# ---------------------------------------------------------------------------
# GET /api/suppliers
# ---------------------------------------------------------------------------
@suppliers_bp.get("/")
def list_suppliers():
    """Return all suppliers, with optional filtering by status or category."""
    status = request.args.get("status")
    category = request.args.get("category")
    query = db.select(Supplier)
    if status:
        if status not in _ALLOWED_STATUSES:
            return jsonify({"error": f"Invalid status. Must be one of: {sorted(_ALLOWED_STATUSES)}"}), 400
        query = query.where(Supplier.status == status)
    if category:
        query = query.where(Supplier.category == category)
    suppliers = db.session.execute(query.order_by(Supplier.name)).scalars().all()
    return jsonify([s.to_dict() for s in suppliers])


# ---------------------------------------------------------------------------
# GET /api/suppliers/search
# ---------------------------------------------------------------------------
@suppliers_bp.get("/search")
def search_suppliers():
    """Full-text search across name, contact_name, and email."""
    q = (request.args.get("q") or "").strip()
    if not q:
        return jsonify({"error": "Query parameter 'q' is required"}), 400
    pattern = f"%{q}%"
    results = (
        db.session.execute(
            db.select(Supplier).where(
                db.or_(
                    Supplier.name.ilike(pattern),
                    Supplier.contact_name.ilike(pattern),
                    Supplier.email.ilike(pattern),
                )
            ).order_by(Supplier.name)
        )
        .scalars()
        .all()
    )
    return jsonify([s.to_dict() for s in results])


# ---------------------------------------------------------------------------
# GET /api/suppliers/<id>
# ---------------------------------------------------------------------------
@suppliers_bp.get("/<int:supplier_id>")
def get_supplier(supplier_id: int):
    supplier, err = _supplier_or_404(supplier_id)
    if err:
        return err
    return jsonify(supplier.to_dict())


# ---------------------------------------------------------------------------
# POST /api/suppliers
# ---------------------------------------------------------------------------
@suppliers_bp.post("/")
def create_supplier():
    """Create a new supplier record."""
    body = request.get_json(silent=True) or {}
    name = (body.get("name") or "").strip()
    if not name:
        return jsonify({"error": "'name' is required"}), 400
    status = body.get("status", "active")
    if status not in _ALLOWED_STATUSES:
        return jsonify({"error": f"Invalid status. Must be one of: {sorted(_ALLOWED_STATUSES)}"}), 400
    supplier = Supplier(
        name=name,
        contact_name=(body.get("contact_name") or "").strip() or None,
        email=(body.get("email") or "").strip() or None,
        phone=(body.get("phone") or "").strip() or None,
        address=(body.get("address") or "").strip() or None,
        category=(body.get("category") or "").strip() or None,
        status=status,
        notes=(body.get("notes") or "").strip() or None,
    )
    db.session.add(supplier)
    db.session.commit()
    return jsonify(supplier.to_dict()), 201


# ---------------------------------------------------------------------------
# PUT /api/suppliers/<id>
# ---------------------------------------------------------------------------
@suppliers_bp.put("/<int:supplier_id>")
def update_supplier(supplier_id: int):
    """Update an existing supplier record."""
    supplier, err = _supplier_or_404(supplier_id)
    if err:
        return err
    body = request.get_json(silent=True) or {}
    updatable = ("name", "contact_name", "email", "phone", "address", "category", "notes")
    for field in updatable:
        if field in body:
            value = (body[field] or "").strip() or None
            if field == "name" and not value:
                return jsonify({"error": "'name' cannot be empty"}), 400
            setattr(supplier, field, value)
    if "status" in body:
        if body["status"] not in _ALLOWED_STATUSES:
            return jsonify({"error": f"Invalid status. Must be one of: {sorted(_ALLOWED_STATUSES)}"}), 400
        supplier.status = body["status"]
    db.session.commit()
    return jsonify(supplier.to_dict())


# ---------------------------------------------------------------------------
# DELETE /api/suppliers/<id>
# ---------------------------------------------------------------------------
@suppliers_bp.delete("/<int:supplier_id>")
def delete_supplier(supplier_id: int):
    """Delete a supplier record."""
    supplier, err = _supplier_or_404(supplier_id)
    if err:
        return err
    db.session.delete(supplier)
    db.session.commit()
    return jsonify({"deleted": supplier_id})
