"""config.py - every setting lives here, read from environment variables."""
import os                                   # lets us read environment variables
from dotenv import load_dotenv              # reads the .env file into environment variables

load_dotenv()                               # run it once so the .env values become available

# --- Database settings ---
DB_URL = os.getenv("DB_URL", "sqlite:///copilot_demo.db")      # the admin connection (used for seeding + audit log)
READONLY_DB_URL = os.getenv("READONLY_DB_URL", DB_URL)         # the SELECT-only connection (used to run AI-written SQL)

# --- LLM settings ---
LLM_PROVIDER = os.getenv("LLM_PROVIDER", "mock").lower()       # "mock", "gemini" or "groq"
GOOGLE_API_KEY = os.getenv("GOOGLE_API_KEY", "")               # Gemini key (empty if unused)
GROQ_API_KEY = os.getenv("GROQ_API_KEY", "")                   # Groq key (empty if unused)
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.0-flash")   # which Gemini model to call
GROQ_MODEL = os.getenv("GROQ_MODEL", "llama-3.3-70b-versatile")  # which Groq model to call

# --- RAG settings ---
RAG_BACKEND = os.getenv("RAG_BACKEND", "keyword").lower()      # "keyword" or "faiss"

# --- Safety limits ---
MAX_ROWS = 1000                             # every query is capped to this many rows
MAX_RETRIES = 2                             # the critic can send SQL back for repair at most twice

# --- Feature switches (evaluate.py flips these to measure each feature's value) ---
USE_SCHEMA_SELECTION = True                 # ask the LLM to pick relevant tables first
USE_GLOSSARY = True                         # give the LLM the business glossary (revenue, margin...)
USE_CRITIC = True                           # let the critic review results and force retries
