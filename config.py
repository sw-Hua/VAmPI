import os
import connexion
from flask import jsonify
from flask_sqlalchemy import SQLAlchemy
from connexion.exceptions import ProblemException

app_instance = connexion.App(__name__, specification_dir='./openapi_specs')

SQLALCHEMY_DATABASE_URI = 'sqlite:///' + os.path.join(app_instance.app.root_path, 'database/database.db')
app_instance.app.config['SQLALCHEMY_DATABASE_URI'] = SQLALCHEMY_DATABASE_URI
app_instance.app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

app_instance.app.config['SECRET_KEY'] = 'random'
# start the db
db = SQLAlchemy(app_instance.app)

def custom_problem_handler(error):
    # Custom error handler for clarity in structure
    response = jsonify({
        "status": "fail",
        "message": getattr(error, "detail", "An error occurred"),
    })
    response.status_code = error.status
    return response
app_instance.add_error_handler(ProblemException, custom_problem_handler)

app_instance.add_api('openapi3.yml')
