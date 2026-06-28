from app import app


SAMPLES = [
    {
        "creator_id": "sample-ai",
        "text": (
            "Artificial intelligence represents a transformative paradigm shift in modern society. "
            "It is important to note that while the benefits of AI are numerous, it is equally "
            "essential to consider the ethical implications. Furthermore, stakeholders across "
            "various sectors must collaborate to ensure responsible deployment."
        ),
    },
    {
        "creator_id": "sample-human",
        "text": (
            "ok so i finally tried that new ramen place downtown and honestly? underwhelming. "
            "the broth was fine but they put WAY too much sodium in it and i was thirsty for "
            "like three hours after. my friend got the spicy version and said it was better. "
            "probably won't go back unless someone drags me there"
        ),
    },
    {
        "creator_id": "sample-borderline-formal",
        "text": (
            "The relationship between monetary policy and asset price inflation has been extensively "
            "studied in the literature. Central banks face a fundamental tension between their mandate "
            "for price stability and the unintended consequences of prolonged low interest rates on "
            "equity and real estate valuations."
        ),
    },
    {
        "creator_id": "sample-borderline-edited",
        "text": (
            "I've been thinking a lot about remote work lately. There are genuine tradeoffs: flexibility "
            "and no commute on one side, isolation and blurred work-life boundaries on the other. Studies "
            "show productivity varies widely by individual and role type."
        ),
    },
]


def main():
    client = app.test_client()
    created = []

    for sample in SAMPLES:
        response = client.post("/submit", json=sample)
        body = response.get_json()
        created.append(body["content_id"])
        print(
            body["creator_id"],
            body["attribution"],
            body["combined_ai_score"],
            body["confidence"],
            body["label"],
        )

    appeal_response = client.post(
        "/appeal",
        json={
            "content_id": created[0],
            "creator_reasoning": (
                "I drafted this myself for class, but I used formal language and transitions "
                "that may have made the passage look synthetic."
            ),
        },
    )
    print("appeal", appeal_response.status_code, appeal_response.get_json()["status"])

    log_response = client.get("/log?limit=5")
    print("log_entries", len(log_response.get_json()["entries"]))


if __name__ == "__main__":
    main()
