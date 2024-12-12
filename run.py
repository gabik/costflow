from app import create_app, db

# Create the Flask app instance
app = create_app()

with app.app_context():
    db.create_all()

if __name__ == "__main__":
    # Run the app in debug mode for development
    app.run(debug=True, host="0.0.0.0", port=8080)

