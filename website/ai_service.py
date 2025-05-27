import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from datetime import datetime

load_dotenv()

class AIService:
    def __init__(self):
        self.embeddings = None
        self.vector_store = None
        self.llm = None
        self.error = None
        self._setup()

    def _setup(self):
        try:
            # Check environment variables
            if not os.getenv('OPENAI_API_KEY'):
                raise ValueError("OPENAI_API_KEY not found in environment")
            
            if not os.getenv('GOOGLE_API_KEY'):
                raise ValueError("GOOGLE_API_KEY not found in environment")
            
            # Initialize embeddings (uses OPENAI_API_KEY from environment)
            self.embeddings = OpenAIEmbeddings(
                model="text-embedding-3-large"
            )

            # Load or create FAISS vector store
            try:
                self.vector_store = FAISS.load_local(
                    "faiss_index",
                    self.embeddings,
                    allow_dangerous_deserialization=True
                )
                print("FAISS index loaded successfully")
            except Exception as e:
                print(f"Failed to load FAISS index: {e}")
                # Create empty vector store if loading fails
                self.vector_store = FAISS.from_texts(
                    texts=["Initial document"],
                    embedding=self.embeddings,
                    metadatas=[{"source": "init"}]
                )
                self.vector_store.save_local("faiss_index")
                print("Created new FAISS index")

            # Initialize LLM (uses GOOGLE_API_KEY from environment)
            self.llm = ChatGoogleGenerativeAI(
                model="gemini-1.5-flash",
                temperature=0.7
            )
            
            print("AI Service initialized successfully")

        except Exception as e:
            self.error = str(e)
            print(f"AI Service initialization failed: {e}")

    def get_ai_response(self, message, conversation_history=None, room_id=None):
        if not self.llm:
            return f"AI service unavailable: {self.error}"

        try:
            # Emit typing indicator if room_id provided
            if room_id:
                try:
                    from . import socketio
                    socketio.emit('ai_typing', {'room_id': room_id}, room=room_id)
                except:
                    pass

            # Search for relevant context
            context = ""
            if self.vector_store and message:
                try:
                    docs = self.vector_store.similarity_search(message, k=3)
                    if docs:
                        # Filter out the initial document
                        relevant_docs = [doc for doc in docs if doc.page_content != "Initial document"]
                        if relevant_docs:
                            context = "\n\n".join(doc.page_content for doc in relevant_docs)
                            print(f"Found {len(relevant_docs)} relevant documents")
                            print(relevant_docs)
                except Exception as e:
                    print(f"Vector search failed: {e}")

            # Build conversation history string
            history_text = ""
            if conversation_history:
                for msg in conversation_history:
                    role = "User" if msg.get('is_from_visitor') else "Assistant"
                    history_text += f"{role}: {msg['content']}\n"

            # Create prompt template with variables
            system_template = (
                "You are a DVC Sales Assistant specialized in Disney Vacation Club (DVC) memberships. "
                "Help with DVC resales, pricing, contracts, and benefits. Focus on the 20%-50% savings "
                "through resales, access to all 14 DVC properties, 6.9% commission rate, and 45-day closing process. "
                "Keep responses professional and DVC-focused. For non-DVC questions, redirect politely or "
                "suggest contacting DVC Sales at 844-382-7253.\n\n"
                "{context}\n\n"
                "{history}"
                "Please provide a helpful response to the user's question."
            )

            prompt = ChatPromptTemplate.from_messages([
                ("system", system_template),
                ("human", "{input}")
            ])

            # Generate response with proper variable injection
            chain = prompt | self.llm
            response = chain.invoke({
                "context": f"Relevant information:\n{context}" if context else "No additional context available.",
                "history": f"Previous conversation:\n{history_text}" if history_text else "",
                "input": message
            })

            # Stop typing indicator
            if room_id:
                try:
                    socketio.emit('ai_stopped_typing', {'room_id': room_id}, room=room_id)
                except:
                    pass

            return self._format_response(response.content)

        except Exception as e:
            # Clean up typing indicator on error
            if room_id:
                try:
                    socketio.emit('ai_stopped_typing', {'room_id': room_id}, room=room_id)
                except:
                    pass
            
            print(f"Error generating AI response: {e}")
            return "I apologize, but I encountered an error. Please try again or contact support."

    def _format_response(self, text):
        if not text:
            return "I apologize, but I couldn't generate a response. Please try again."
        
        # Basic cleanup
        text = text.strip()
        text = text.replace('\r\n', '\n').replace('\n\n\n', '\n\n')
        
        # Ensure proper capitalization and ending
        if text and text[0].islower():
            text = text[0].upper() + text[1:]
        if text and text[-1] not in '.!?':
            text += '.'
            
        return text[:1000]  # Limit response length

    def add_documents(self, text, source_name="user_upload"):
        """Add text to the knowledge base"""
        try:
            if not text or not self.vector_store:
                return 0, "Invalid input or vector store not available"

            # Create document
            document = Document(
                page_content=text,
                metadata={"source": source_name, "date_added": str(datetime.now())}
            )

            # Split into chunks
            splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=100
            )
            chunks = splitter.split_documents([document])

            # Add to vector store
            self.vector_store.add_documents(chunks)
            self.vector_store.save_local("faiss_index")

            return len(chunks), f"Successfully added {len(chunks)} document chunks"

        except Exception as e:
            print(f"Error adding documents: {e}")
            return 0, f"Error: {str(e)}"

    def get_status(self):
        """Get service status"""
        return {
            'llm_ready': self.llm is not None,
            'vector_store_ready': self.vector_store is not None,
            'embeddings_ready': self.embeddings is not None,
            'error': self.error,
            'faiss_index_exists': os.path.exists("faiss_index")
        }

# Global instance
ai_service = AIService()