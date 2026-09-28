"""Seed script for populating initial transaction data into Firestore."""

from google.cloud import firestore

# Hardcode GCP Project ID directly for Firestore client
PROJECT_ID = "qwiklabs-gcp-01-806236dd18bf"

db = firestore.Client(project=PROJECT_ID)
collection_ref = db.collection("transactions")

INITIAL_TRANSACTIONS = [
    {
        "description": "Whole Foods Market",
        "amount": 124.50,
        "category": "Groceries",
        "date": "2026-09-20",
        "payment_method": "Credit Card",
    },
    {
        "description": "Starbucks Coffee",
        "amount": 6.75,
        "category": "Dining",
        "date": "2026-09-21",
        "payment_method": "Debit Card",
    },
    {
        "description": "Monthly Apartment Rent",
        "amount": 1850.00,
        "category": "Housing",
        "date": "2026-09-22",
        "payment_method": "Bank Transfer",
    },
    {
        "description": "Electric Utility Bill",
        "amount": 92.40,
        "category": "Utilities",
        "date": "2026-09-24",
        "payment_method": "Credit Card",
    },
    {
        "description": "Netflix Subscription",
        "amount": 15.99,
        "category": "Entertainment",
        "date": "2026-09-25",
        "payment_method": "Credit Card",
    },
    {
        "description": "Trader Joe's",
        "amount": 68.30,
        "category": "Groceries",
        "date": "2026-09-26",
        "payment_method": "Credit Card",
    },
]


def seed_database():
    print(f"Seeding Firestore collection 'transactions' in project: {PROJECT_ID}...")
    for txn in INITIAL_TRANSACTIONS:
        doc_ref = collection_ref.add(txn)
        print(f"Added transaction '{txn['description']}' with ID: {doc_ref[1].id}")
    print("Seeding completed successfully!")


if __name__ == "__main__":
    seed_database()
