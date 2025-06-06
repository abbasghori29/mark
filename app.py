from website import create_app
import os
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

app = create_app()

if __name__ == '__main__':
    #app.run(host='127.0.0.1', port=5000)
    #app.run(host='0.0.0.0', port=5000)
    app.run(debug=True)