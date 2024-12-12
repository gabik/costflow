# Use an official lightweight Python image as the base
FROM python:3.10-slim

# Set the working directory inside the container
WORKDIR /app

# Copy the requirements file into the container
COPY requirements.txt .

# Install the Python dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the application code into the container
COPY . .

# Expose the port Flask will run on
EXPOSE 8080

# Set the environment variable for Flask
ENV FLASK_APP=run.py

# Define the command to run the Flask app
CMD ["flask", "run", "--host=0.0.0.0", "--port=8080"]

