from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from motor.motor_asyncio import AsyncIOMotorClient
from config import settings
from models import Question, SimilarityResponse, QuestionInput, SimilarityRequest, WordCheckRequest, WordCheckResponse
from datetime import datetime
from bson import ObjectId
import spacy

app = FastAPI(
    title="Questions API",
    description="API for managing and checking similarity of questions",
    version="1.0.0"
)
nlp = spacy.load('en_core_web_sm')

# Update CORS middleware configuration
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,  # Changed to False since we're using allow_origins=["*"]
    allow_methods=["GET", "POST", "PUT", "DELETE", "OPTIONS"],  # Explicitly include OPTIONS
    allow_headers=["*"],
    max_age=86400,  # Cache preflight requests for 24 hours
)

@app.on_event("startup")
async def startup_db_client():
    app.mongodb_client = AsyncIOMotorClient(settings.MONGODB_URL)
    app.mongodb = app.mongodb_client[settings.DATABASE_NAME]
    
    pipeline = [
        {"$group": {
            "_id": "$text",
            "duplicate_ids": {"$addToSet": "$_id"}, 
            "count": {"$sum": 1} 
        }},
        {"$match": {"count": {"$gt": 1}}}
    ]

    cursor = app.mongodb.questions.aggregate(pipeline)
    async for doc in cursor:
        duplicate_ids = doc["duplicate_ids"]
        duplicate_ids.pop(0)  # Keep the first one, remove others
        await app.mongodb.questions.delete_many({"_id": {"$in": duplicate_ids}})
    
    print("Duplicate questions removed successfully")

@app.on_event("shutdown")
async def shutdown_db_client():
    app.mongodb_client.close()

@app.post("/questions/", response_model=Question)
async def create_question(question: QuestionInput):
    # Check if there are already 10 questions
    count = await app.mongodb.questions.count_documents({})
    
    question_data = {
        "text": question.text,
        "created_at": datetime.utcnow()
    }
    
    result = await app.mongodb.questions.insert_one(question_data)
    return Question(
        id=str(result.inserted_id),
        text=question.text,
        created_at=question_data["created_at"]
    )

@app.delete("/questions/{question_id}")
async def delete_question(question_id: str):
    result = await app.mongodb.questions.delete_one({"_id": ObjectId(question_id)})
    if result.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Question not found")
    return {"message": "Question deleted successfully"}

@app.get("/questions/")
async def get_questions():
    count = await app.mongodb.questions.count_documents({})
    return {"total_questions": count}

@app.post("/questions/check-similarity/", response_model=SimilarityResponse)
async def check_similarity(question: SimilarityRequest):
    input_doc = nlp(question.text)
    similar_questions = []
    
    cursor = app.mongodb.questions.find({})
    async for existing_question in cursor:
        existing_doc = nlp(existing_question["text"])
        similarity = input_doc.similarity(existing_doc)
        
        if similarity > 0.7:
            similar_questions.append({
                "id": str(existing_question["_id"]),
                "text": existing_question["text"],
                "created_at": existing_question["created_at"],
                "score": round(similarity * 100, 2)
            })
    
    return SimilarityResponse(
        similar_questions=similar_questions,
        similarity_count=len(similar_questions)
    )
    
@app.post("/questions/check-similarity-2/", response_model=SimilarityResponse)
async def check_similarity(question: SimilarityRequest):
    cursor = app.mongodb.questions.find(
            {"$text": {"$search": question.text}},
            {"score": {"$meta": "textScore"}}
        ).sort([("score", {"$meta": "textScore"})])
    
    filtered_results = []
    async for doc in cursor:
        if doc.get("score", 0) > 2.5:
            # Convert ObjectId to string before adding to results
            doc["_id"] = str(doc["_id"])
            filtered_results.append(doc)
    
    return SimilarityResponse(
        similar_questions=filtered_results,
        similarity_count=len(filtered_results)
    )

@app.post("/questions/check-words/", response_model=WordCheckResponse)
async def check_words(request: WordCheckRequest):
    input_words = set(word.lower() for word in request.text.split())
    match_count = 0
    cursor = app.mongodb.questions.find({})
    
    async for existing_question in cursor:
        question_words = set(word.lower() for word in existing_question["text"].split())
        common_words = input_words.intersection(question_words)
        
        if common_words:
            match_count += 1
    
    return WordCheckResponse(
        match_count=match_count
    )

@app.get("/",
    summary="API Root",
    description="Redirects to API documentation"
)
async def root():
    from fastapi.responses import RedirectResponse
    return RedirectResponse(url="/docs")
