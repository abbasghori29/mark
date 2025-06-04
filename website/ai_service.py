import os
from dotenv import load_dotenv
from langchain_google_genai import ChatGoogleGenerativeAI
from langchain_openai import OpenAIEmbeddings
from langchain_core.prompts import ChatPromptTemplate
from langchain_community.vectorstores import FAISS
from langchain.text_splitter import RecursiveCharacterTextSplitter
from langchain_core.documents import Document
from langchain_community.document_loaders import CSVLoader
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
                chunk_overlap=200
            )
            chunks = splitter.split_documents([document])

            # Add to vector store
            self.vector_store.add_documents(chunks)
            self.vector_store.save_local("faiss_index")

            return len(chunks), f"Successfully added {len(chunks)} document chunks"

        except Exception as e:
            print(f"Error adding documents: {e}")
            return 0, f"Error: {str(e)}"

    def process_text_for_knowledge_base(self, text, source_name="user_upload"):
        """Process text for knowledge base - wrapper for add_documents for backward compatibility"""
        return self.add_documents(text, source_name)

    def load_csv_data(self, file_path):
        """Load CSV file and return documents"""
        try:
            loader = CSVLoader(file_path=file_path, encoding='utf-8')
            documents = loader.load()
            print(f"Loaded {len(documents)} documents from CSV file")
            return documents
        except Exception as e:
            print(f"Error loading CSV file: {e}")
            raise e

    def split_documents(self, documents):
        """Split documents into chunks"""
        try:
            text_splitter = RecursiveCharacterTextSplitter(
                chunk_size=1000,
                chunk_overlap=200,
                length_function=len,
            )
            split_docs = text_splitter.split_documents(documents)
            print(f"Split into {len(split_docs)} chunks")
            return split_docs
        except Exception as e:
            print(f"Error splitting documents: {e}")
            raise e

    def create_faiss_db_from_documents(self, split_docs):
        """Create FAISS database from split documents"""
        try:
            if not self.embeddings:
                raise ValueError("Embeddings not initialized")

            # Create FAISS vector store from documents
            db = FAISS.from_documents(split_docs, self.embeddings)
            print(f"Created FAISS database with {len(split_docs)} documents")
            return db
        except Exception as e:
            print(f"Error creating FAISS database: {e}")
            raise e

    def process_csv_for_knowledge_base(self, csv_file_path, source_name="csv_upload"):
        """Process CSV file and add to knowledge base"""
        try:
            if not os.path.exists(csv_file_path):
                return 0, f"Error: CSV file {csv_file_path} not found!"

            # Step 1: Load CSV data
            print("Loading CSV file...")
            documents = self.load_csv_data(csv_file_path)

            if not documents:
                return 0, "No documents found in CSV file"

            # Add source metadata to all documents
            for doc in documents:
                doc.metadata.update({
                    "source": source_name,
                    "date_added": str(datetime.now()),
                    "file_type": "csv"
                })

            # Step 2: Split documents
            print("Splitting documents...")
            split_docs = self.split_documents(documents)

            # Step 3: Add to existing vector store or create new one
            if self.vector_store:
                print("Adding to existing FAISS database...")
                self.vector_store.add_documents(split_docs)
            else:
                print("Creating new FAISS database...")
                self.vector_store = self.create_faiss_db_from_documents(split_docs)

            # Step 4: Save FAISS index
            print("Saving FAISS index...")
            self.vector_store.save_local("faiss_index")
            print("FAISS database updated and saved successfully!")

            return len(split_docs), f"Successfully processed CSV and added {len(split_docs)} document chunks"

        except Exception as e:
            print(f"Error processing CSV for knowledge base: {e}")
            return 0, f"Error: {str(e)}"

    def rebuild_knowledge_base_from_csv(self, csv_file_path, source_name="csv_rebuild"):
        """Rebuild entire knowledge base from CSV file (replaces existing data)"""
        try:
            if not os.path.exists(csv_file_path):
                return 0, f"Error: CSV file {csv_file_path} not found!"

            # Get OpenAI API key from environment
            openai_api_key = os.getenv("OPENAI_API_KEY")
            if not openai_api_key:
                return 0, "Error: OPENAI_API_KEY not found in environment variables"

            # Step 1: Load CSV data
            print("Loading CSV file...")
            documents = self.load_csv_data(csv_file_path)

            if not documents:
                return 0, "No documents found in CSV file"

            # Add source metadata to all documents
            for doc in documents:
                doc.metadata.update({
                    "source": source_name,
                    "date_added": str(datetime.now()),
                    "file_type": "csv"
                })

            # Step 2: Split documents
            print("Splitting documents...")
            split_docs = self.split_documents(documents)

            # Step 3: Create new FAISS database (this replaces the existing one)
            print("Creating new FAISS database...")
            embeddings = OpenAIEmbeddings(
                model="text-embedding-3-small",
                openai_api_key=openai_api_key
            )

            db = FAISS.from_documents(split_docs, embeddings)

            # Step 4: Save FAISS index
            print("Saving FAISS index...")
            db.save_local("faiss_index")

            # Update our instance
            self.embeddings = embeddings
            self.vector_store = db

            print("Knowledge base rebuilt successfully from CSV!")
            return len(split_docs), f"Successfully rebuilt knowledge base with {len(split_docs)} document chunks from CSV"

        except Exception as e:
            print(f"Error rebuilding knowledge base from CSV: {e}")
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