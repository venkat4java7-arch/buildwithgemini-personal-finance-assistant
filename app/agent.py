# ruff: noqa
# Copyright 2026 Google LLC
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     https://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import datetime
import json
import os
import urllib.request
import uuid
from zoneinfo import ZoneInfo

from a2ui.basic_catalog.provider import BasicCatalog
from a2ui.schema.manager import A2uiSchemaManager
from google import genai
from google.adk.agents import Agent
from google.adk.apps import App
from google.adk.code_executors import AgentEngineSandboxCodeExecutor
from google.adk.memory.vertex_ai_memory_bank_service import VertexAiMemoryBankService
from google.adk.models import Gemini
from google.adk.tools import ToolContext
from google.cloud import firestore, storage
from google.genai import types

from .a2ui_utils import a2ui_callback

# Hardcode GCP Project ID and Cloud Storage Bucket directly as strings
PROJECT_ID = "qwiklabs-gcp-01-806236dd18bf"
BUCKET_NAME = "personal-finance-assets-806236dd"

db = firestore.Client(project=PROJECT_ID)

# Load Agent Engine resource name from deployment_metadata.json if available
deployment_metadata_file = os.path.join(
    os.path.dirname(__file__), "..", "deployment_metadata.json"
)
agent_engine_resource_name = None
if os.path.exists(deployment_metadata_file):
    try:
        with open(deployment_metadata_file, "r") as f:
            metadata = json.load(f)
            agent_engine_resource_name = metadata.get("remote_agent_runtime_id")
    except Exception:
        pass

# Initialize AgentEngineSandboxCodeExecutor with the Agent Engine resource name
code_executor = AgentEngineSandboxCodeExecutor(
    agent_engine_resource_name=agent_engine_resource_name
)

# Extract Agent Engine ID for Vertex AI Memory Bank Service
agent_engine_id = (
    agent_engine_resource_name.split("/")[-1]
    if agent_engine_resource_name
    else "1124059324377726976"
)

# Configure Vertex AI Memory Bank Service for future production redeployments
memory_service = VertexAiMemoryBankService(
    project=PROJECT_ID,
    location="us-east1",
    agent_engine_id=agent_engine_id,
)

# Build A2UI system prompt using A2uiSchemaManager v0.8 and BasicCatalog
schema_manager = A2uiSchemaManager(
    version="0.8",
    catalogs=[BasicCatalog.get_config("0.8")],
)

instruction = schema_manager.generate_system_prompt(
    role_description=(
        "You are a Personal Finance Assistant. You help users track expenses, "
        "manage monthly budgets, set spending category limits, analyze spending, "
        "look up live foreign exchange rates, generate goal milestone concept images, and record new transactions. "
        "You can run Python code using your code executor to compute compound interest projections, budget calculations, and savings timelines. "
        "Use your tools to query transactions, add new expenses, set budget limits, get exchange rates, generate milestone images, and generate budget summaries."
    ),
    workflow_description="Analyze the request and return structured UI when appropriate.",
    ui_description=(
        "Keep every surface tiny and flat: ONE Card > ONE Column > a few Text rows. "
        "Never nest a Card inside a Card. "
        "Use ONLY these components: Card, Column, Row, Text, and Image. Do not use "
        "Table or Heading (unsupported), or Buttons, actions, or forms (they do "
        "nothing in adk web). "
        "You may include one Image component, but only when you have a public https "
        "URL for the image (for example the URL an image tool returns after uploading "
        "to a public bucket). Set the Image url to that exact https link, for example "
        '{"Image": {"url": {"literalString": "https://..."}}}. Never point an '
        "Image at a bare filename, an artifact name, or a non-http(s) path. If you do "
        "not have a public URL, add a short Text line noting the image instead. "
        "No markdown in text; use the usageHint property ('h1', 'h2', 'body') for "
        "headings and emphasis. "
        "Output ONLY the raw A2UI JSON array — no prose, and never wrap it in "
        "<a2a_datapart_json> tags or 'kind'/'data'/'metadata' objects."
    ),
    include_schema=True,
    include_examples=True,
)


def get_recent_transactions(category: str = "", limit: int = 10) -> str:
    """Reads recent transactions from the Firestore database.

    Args:
        category: Optional category filter (e.g. 'Groceries', 'Dining', 'Housing', 'Utilities', 'Entertainment').
        limit: Maximum number of transactions to retrieve.

    Returns:
        A JSON string containing the list of matching transactions.
    """
    try:
        query_ref = db.collection("transactions")
        if category:
            query_ref = query_ref.where("category", "==", category)
        docs = query_ref.limit(limit).stream()

        transactions = []
        for doc in docs:
            data = doc.to_dict()
            data["id"] = doc.id
            transactions.append(data)

        if not transactions:
            return f"No transactions found{' for category ' + category if category else ''}."

        return json.dumps(transactions, indent=2)
    except Exception as e:
        return f"Error retrieving transactions: {str(e)}"


def add_transaction(
    description: str,
    amount: float,
    category: str,
    date: str = "",
    payment_method: str = "Credit Card",
) -> str:
    """Adds a new expense transaction to the Firestore database.

    Args:
        description: Description of the expense (e.g. 'Whole Foods Market', 'Coffee').
        amount: Dollar amount of the expense (e.g. 45.50).
        category: Category of the expense (e.g. 'Groceries', 'Dining', 'Housing', 'Utilities', 'Entertainment').
        date: Date of transaction in YYYY-MM-DD format. Defaults to today's date if empty.
        payment_method: Payment method used (e.g. 'Credit Card', 'Debit Card', 'Bank Transfer').

    Returns:
        A message confirming the transaction addition with its generated document ID.
    """
    try:
        if not date:
            date = datetime.date.today().strftime("%Y-%m-%d")

        txn_data = {
            "description": description,
            "amount": float(amount),
            "category": category,
            "date": date,
            "payment_method": payment_method,
        }

        doc_ref = db.collection("transactions").add(txn_data)
        doc_id = doc_ref[1].id
        return f"Successfully recorded transaction '{description}' (${amount:.2f}) under {category} with ID {doc_id}."
    except Exception as e:
        return f"Error adding transaction: {str(e)}"


def get_budget_summary() -> str:
    """Calculates total spending and category breakdown from Firestore transactions.

    Returns:
        A summary report of total expenses and totals per category.
    """
    try:
        docs = db.collection("transactions").stream()
        total_spending = 0.0
        category_totals = {}

        count = 0
        for doc in docs:
            data = doc.to_dict()
            amt = float(data.get("amount", 0.0))
            cat = data.get("category", "Uncategorized")

            total_spending += amt
            category_totals[cat] = category_totals.get(cat, 0.0) + amt
            count += 1

        summary = {
            "total_transactions": count,
            "total_spending": round(total_spending, 2),
            "category_breakdown": {
                k: round(v, 2) for k, v in category_totals.items()
            },
        }
        return json.dumps(summary, indent=2)
    except Exception as e:
        return f"Error calculating budget summary: {str(e)}"


def calculate(expression: str) -> str:
    """Evaluates a basic mathematical expression safely.

    Args:
        expression: A string containing a math expression to evaluate, e.g., '15 * 4 + 10'.

    Returns:
        A string containing the calculated result.
    """
    try:
        allowed_names = {"abs": abs, "round": round}
        code = compile(expression, "<string>", "eval")
        for name in code.co_names:
            if name not in allowed_names:
                raise NameError(f"Use of {name} is not allowed")
        result = eval(code, {"__builtins__": {}}, allowed_names)
        return f"Result: {result}"
    except Exception as e:
        return f"Error evaluating expression: {str(e)}"


def set_category_budget_limit(category: str, monthly_limit: float) -> str:
    """Sets or updates the monthly budget limit for a specific spending category in Firestore.

    Args:
        category: The spending category (e.g. 'Dining', 'Groceries', 'Entertainment', 'Utilities', 'Housing').
        monthly_limit: The maximum allowed monthly limit in dollars (e.g. 250.00).

    Returns:
        A confirmation message stating the updated budget limit.
    """
    try:
        db.collection("budgets").document(category.lower()).set({
            "category": category,
            "monthly_limit": float(monthly_limit),
            "updated_at": datetime.date.today().strftime("%Y-%m-%d"),
        })
        return f"Successfully set monthly budget limit for '{category}' to ${monthly_limit:.2f}."
    except Exception as e:
        return f"Error setting budget limit: {str(e)}"


def get_exchange_rates(base_currency: str = "USD") -> str:
    """Fetches real-time foreign exchange conversion rates for a specified base currency.

    Args:
        base_currency: 3-letter currency code (e.g. 'USD', 'EUR', 'GBP', 'JPY', 'CAD').

    Returns:
        A JSON string containing real-time exchange rates.
    """
    try:
        api_key = os.getenv("EXCHANGE_RATE_API_KEY", "")
        if api_key:
            url = f"https://v6.exchangerate-api.com/v6/{api_key}/latest/{base_currency.upper()}"
        else:
            url = f"https://open.er-api.com/v6/latest/{base_currency.upper()}"

        req = urllib.request.Request(url, headers={"User-Agent": "PersonalFinanceAssistant/1.0"})
        with urllib.request.urlopen(req, timeout=5) as response:
            data = json.loads(response.read().decode())

        if data.get("result") == "success":
            rates = data.get("rates", {})
            major_currencies = ["USD", "EUR", "GBP", "CAD", "JPY", "AUD", "CHF", "CNY", "INR"]
            filtered_rates = {k: rates[k] for k in major_currencies if k in rates}
            return json.dumps({
                "base": base_currency.upper(),
                "last_updated": data.get("time_last_update_utc"),
                "rates": filtered_rates,
            }, indent=2)
        else:
            return f"Failed to retrieve exchange rates for {base_currency}."
    except Exception as e:
        return f"Error fetching exchange rates: {str(e)}"


async def generate_goal_milestone_image(
    tool_context: ToolContext, prompt: str
) -> str:
    """Generates a visual concept image for a personal finance goal or milestone using the gemini-3.1-flash-lite-image model in global region.

    Saves the image as an ADK artifact and uploads it to public Cloud Storage.

    Args:
        prompt: Description of the financial goal or milestone image (e.g., 'Concept art for a savings target of 10,000 dollars for a house down payment').

    Returns:
        The public HTTPS URL of the generated image in Cloud Storage.
    """
    try:
        # 1. Generate image using gemini-3.1-flash-lite-image in global region
        image_client = genai.Client(
            vertexai=True, project=PROJECT_ID, location="global"
        )
        res = image_client.models.generate_content(
            model="gemini-3.1-flash-lite-image", contents=prompt
        )

        part = res.candidates[0].content.parts[0]
        image_bytes = part.inline_data.data
        mime_type = part.inline_data.mime_type or "image/png"

        ext = "png" if "png" in mime_type else "jpg"
        filename = f"goal_milestone_{uuid.uuid4().hex[:8]}.{ext}"

        # 2. Save image as an ADK session artifact (shows up in Playground Artifacts panel)
        artifact_part = types.Part.from_bytes(
            data=image_bytes, mime_type=mime_type
        )
        await tool_context.save_artifact(
            filename=filename, artifact=artifact_part
        )

        # 3. Upload image bytes directly to public Cloud Storage bucket
        storage_client = storage.Client(project=PROJECT_ID)
        bucket = storage_client.bucket(BUCKET_NAME)
        blob = bucket.blob(filename)
        blob.upload_from_string(image_bytes, content_type=mime_type)

        public_url = f"https://storage.googleapis.com/{BUCKET_NAME}/{filename}"
        return public_url
    except Exception as e:
        return f"Error generating goal milestone image: {str(e)}"


root_agent = Agent(
    name="root_agent",
    model=Gemini(
        model="gemini-flash-latest",
        retry_options=types.HttpRetryOptions(attempts=3),
    ),
    instruction=instruction,
    code_executor=code_executor,
    tools=[
        get_recent_transactions,
        add_transaction,
        get_budget_summary,
        set_category_budget_limit,
        get_exchange_rates,
        generate_goal_milestone_image,
        calculate,
    ],
    after_model_callback=a2ui_callback,
)

app = App(
    root_agent=root_agent,
    name="app",
)
