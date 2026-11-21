import re
import requests


OLLAMA_URL = "http://localhost:11434/api/chat"
MODEL_NAME = "qwen3:4b"


def generate_answer(question: str, context: str) -> str:
    """Генерирует краткий ответ на основе контекста документа."""

    prompt = f"""
/no_think

Ты помощник по анализу документов.

Правила:
1. Отвечай только на основании контекста.
2. Не придумывай факты и значения.
3. Если информации недостаточно, прямо сообщи об этом.
4. Отвечай кратко и по существу на русском языке.
5. Не показывай рассуждения, анализ или ход решения.
6. Сразу выдавай готовый ответ.

Контекст документа:
{context}

Вопрос пользователя:
{question}

Дай только итоговый ответ.
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1
            }
        },
        timeout=180
    )

    response.raise_for_status()

    data = response.json()
    answer = data["message"]["content"].strip()

    # Убираем блок внутренних рассуждений, если модель его вернула.
    if "</think>" in answer:
        answer = answer.rsplit("</think>", 1)[-1].strip()

    answer = re.sub(
        r"<think>.*?</think>",
        "",
        answer,
        flags=re.DOTALL
    ).strip()

    return answer



def generate_edited_text(
    instruction: str,
    document_text: str,
) -> str:
    """
    Подготавливает новую редакцию документа,
    сохраняя структуру абзацев и таблиц.
    """

    prompt = f"""
/no_think

Ты профессиональный редактор документов.

Твоя задача — изменить документ строго по инструкции пользователя.

ПРАВИЛА РЕДАКТИРОВАНИЯ:

1. Сохраняй весь исходный текст, который не требуется менять.
2. Изменяй только сведения, указанные пользователем.
3. Не придумывай новые факты, даты, суммы и условия.
4. Не удаляй и не добавляй разделы документа.
5. Сохраняй исходный порядок абзацев и строк таблиц.
6. Не добавляй новые абзацы и строки таблиц.
7. Не меняй количество столбцов таблиц.
8. Не меняй заголовки таблиц без прямого указания пользователя.
9. Сохраняй разделитель | между ячейками таблиц.
10. Каждую строку таблицы возвращай отдельной строкой.
11. Не удаляй пустые ячейки и разделители таблиц.
12. Если инструкция неоднозначна или противоречива,
    не изменяй документ.
13. Не добавляй пояснения, комментарии и Markdown.

ФОРМАТ:

Обычный текст возвращай обычными строками.

Строки таблиц возвращай в формате:
Ячейка 1 | Ячейка 2 | Ячейка 3

Исходный документ:
{document_text}

Инструкция пользователя:
{instruction}

Верни только полный изменённый текст документа.
"""

    response = requests.post(
        OLLAMA_URL,
        json={
            "model": MODEL_NAME,
            "messages": [
                {
                    "role": "user",
                    "content": prompt
                }
            ],
            "stream": False,
            "think": False,
            "options": {
                "temperature": 0.1
            }
        },
        timeout=180
    )

    response.raise_for_status()

    data = response.json()
    edited_text = data["message"]["content"].strip()

    if "</think>" in edited_text:
        edited_text = edited_text.rsplit("</think>", 1)[-1].strip()

    edited_text = re.sub(
        r"<think>.*?</think>",
        "",
        edited_text,
        flags=re.DOTALL
    ).strip()

    return edited_text
