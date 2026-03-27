import re
import unicodedata


def _normalize_text(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text or "")
    ascii_text = normalized.encode("ascii", "ignore").decode("ascii").lower()
    ascii_text = re.sub(r"\s+", " ", ascii_text).strip()
    return ascii_text


# ---------------------------------------------------------------------------
# Keyword dictionaries
# ---------------------------------------------------------------------------
# Each entry: canonical skill name -> tuple of keywords.
# Keywords match against the lowered + ASCII-normalized description, so
# umlauts are already stripped (Munchen, not München).
#
# German job postings often mix English technical terms with German prose,
# so we include both.  Keywords are ordered roughly from most-specific to
# least-specific; the first match wins per skill.
# ---------------------------------------------------------------------------

TECHNICAL_RULES: dict[str, tuple[str, ...]] = {
    # -- Programming & scripting --
    "Python": ("python",),
    "R": (
        " r ", " r,", "(r)", " r.", "r-programmierung", "r programming",
        "rstudio", "r studio", "r-studio",
    ),
    "SQL": ("sql", "structured query language"),
    "Java": (" java ", " java,", "(java)", "java-entwickl",),
    "Scala": (" scala ", " scala,", "(scala)",),
    "JavaScript": ("javascript", "js ", "node.js", "nodejs",),
    "TypeScript": ("typescript",),
    "C#": ("c#", "c-sharp", "csharp",),
    "C++": ("c++",),
    "VBA": ("vba", "visual basic",),
    "SAS": (" sas ", " sas,", "(sas)",),
    "SPSS": ("spss",),
    "MATLAB": ("matlab",),
    "Bash / Shell": ("bash", "shell script",),
    "Go": (" go ", " golang",),
    "Rust": (" rust ",),

    # -- BI & visualisation --
    "Power BI": ("power bi", "powerbi", "microsoft power bi", "power-bi",),
    "Tableau": ("tableau",),
    "Looker": ("looker",),
    "Qlik": ("qlik", "qlikview", "qliksense", "qlik sense",),
    "MicroStrategy": ("microstrategy",),
    "Metabase": ("metabase",),
    "Grafana": ("grafana",),
    "SAP Analytics Cloud": ("sap analytics cloud", "sac ",),
    "Dashboarding": (
        "dashboard", "dashboards", "dashboarding",
        "berichtswesen", "berichterstattung",
    ),
    "Reporting": ("reporting", "berichte erstellen", "report-erstellung",),

    # -- Data engineering & pipelines --
    "ETL": (
        "etl", "elt ", "data pipeline", "datenpipeline", "datenintegration",
        "data integration", "data warehousing", "data warehouse",
        "datenaufbereitung",
    ),
    "dbt": ("dbt",),
    "Apache Spark": ("spark", "pyspark", "apache spark",),
    "Apache Kafka": ("kafka",),
    "Apache Airflow": ("airflow",),
    "Luigi": ("luigi",),
    "Prefect": ("prefect",),
    "Dagster": ("dagster",),
    "Informatica": ("informatica",),
    "Talend": ("talend",),
    "SSIS": ("ssis",),
    "Fivetran": ("fivetran",),

    # -- Databases --
    "PostgreSQL": ("postgresql", "postgres",),
    "MySQL": ("mysql",),
    "SQL Server": ("sql server", "mssql", "microsoft sql",),
    "Oracle DB": ("oracle db", "oracle database", "oracle-datenbank",),
    "MongoDB": ("mongodb", "mongo db",),
    "Redis": ("redis",),
    "Snowflake": ("snowflake",),
    "BigQuery": ("bigquery", "big query",),
    "Redshift": ("redshift",),
    "Databricks": ("databricks",),
    "DynamoDB": ("dynamodb",),
    "Cassandra": ("cassandra",),

    # -- Cloud --
    "Azure": ("azure", "microsoft azure",),
    "AWS": ("aws", "amazon web services",),
    "GCP": ("gcp", "google cloud",),

    # -- Enterprise / ERP --
    "SAP": (
        "sap ", "sap,", "sap.", "(sap)", "sap-", "sap/",
        "s/4hana", "s4hana", "sap hana",
    ),
    "SAP BW": ("sap bw", "sap business warehouse",),
    "Salesforce": ("salesforce",),
    "ServiceNow": ("servicenow",),

    # -- Data science & ML --
    "Machine Learning": (
        "machine learning", "maschinelles lernen", "ml-modell",
        "ml model",
    ),
    "Deep Learning": ("deep learning", "neural network", "neuronales netz",),
    "NLP": ("nlp", "natural language processing", "textanalyse",),
    "Computer Vision": ("computer vision", "bildverarbeitung",),
    "TensorFlow": ("tensorflow",),
    "PyTorch": ("pytorch",),
    "Scikit-learn": ("scikit-learn", "scikit learn", "sklearn",),
    "XGBoost": ("xgboost",),
    "LLM": ("llm", "large language model", "generative ai", "generative ki",),

    # -- Python ecosystem --
    "Pandas": ("pandas",),
    "NumPy": ("numpy",),
    "Matplotlib": ("matplotlib",),
    "Seaborn": ("seaborn",),
    "Plotly": ("plotly",),
    "Scipy": ("scipy",),
    "Jupyter": ("jupyter",),
    "Streamlit": ("streamlit",),

    # -- Excel & spreadsheets --
    "Excel": (
        "excel", "spreadsheet", "tabellenkalkulation",
        "pivot", "vlookup", "sverweis",
    ),
    "Google Sheets": ("google sheets", "google tabellen",),

    # -- Analytics concepts --
    "Forecasting": (
        "forecast", "forecasting", "prognose", "vorhersage",
    ),
    "A/B Testing": ("a/b test", "ab test", "a/b-test",),
    "Statistics": (
        "statisti", "regression", "hypothes",
        "wahrscheinlichkeit", "probability",
    ),
    "Data Modeling": (
        "data model", "datenmodell", "dimensional model",
        "star schema", "snowflake schema",
    ),
    "Data Governance": (
        "data governance", "datenqualitat", "data quality",
        "master data", "stammdaten",
    ),

    # -- DevOps / tools --
    "Git": (" git ", " git,", "github", "gitlab", "bitbucket",),
    "Docker": ("docker",),
    "Kubernetes": ("kubernetes", "k8s",),
    "CI/CD": ("ci/cd", "cicd", "continuous integration", "continuous delivery",),
    "Terraform": ("terraform",),
    "Linux": ("linux",),
    "Jira": ("jira",),
    "Confluence": ("confluence",),

    # -- Methodology --
    "Agile / Scrum": (
        "agile", "scrum", "kanban", "sprint",
    ),
    "Six Sigma": ("six sigma", "lean six",),
}

SOFT_RULES: dict[str, tuple[str, ...]] = {
    "Stakeholder Communication": (
        "stakeholder", "prasentation", "presentation",
        "geschaftsfuhrung", "management reporting",
    ),
    "Problem Solving": (
        "problem solving", "problem-solving", "problemlosung", "analytisches denken",
    ),
    "Teamwork": ("teamarbeit", "zusammenarbeit", "collaboration", "collaborate",),
    "Project Management": (
        "projektmanagement", "project management", "projektleitung",
    ),
    "Leadership": (
        "fuhrung", "leadership", "teamleitung", "team lead",
    ),
}

LANGUAGE_RULES: dict[str, tuple[str, ...]] = {
    "German (Deutsch)": (
        "deutsch", "german", "deutschkenntnisse", "muttersprache",
    ),
    "English": (
        "englisch", "english", "englishkenntnisse",
    ),
}


def extract_skills(description: str) -> list[dict]:
    """Rule-based skill extractor for German + English job descriptions.

    Returns a list like ``[{"skill_name": "Python", "category": "technical"}, ...]``.
    """
    if not description:
        return []

    lowered = _normalize_text(description)

    extracted: list[dict] = []
    seen: set[str] = set()

    def _add_matches(rules: dict[str, tuple[str, ...]], category: str) -> None:
        for skill_name, keywords in rules.items():
            if skill_name in seen:
                continue
            if any(kw in lowered for kw in keywords):
                extracted.append({"skill_name": skill_name, "category": category})
                seen.add(skill_name)

    _add_matches(TECHNICAL_RULES, "technical")
    _add_matches(SOFT_RULES, "soft")
    _add_matches(LANGUAGE_RULES, "language")

    return extracted
