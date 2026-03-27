from app.services.skill_extractor import extract_skills


def test_extract_skills_detects_synonyms():
    text = "Need experience in Microsoft Power BI, data warehousing, SQL and stakeholder communication."
    found = {item["skill_name"] for item in extract_skills(text)}
    assert "Power BI" in found
    assert "ETL" in found
    assert "SQL" in found
    assert "Stakeholder Communication" in found
