from flask import Flask, render_template, request, session
from flask_session import Session
import os
import openai

app = Flask(__name__)
app.config["SESSION_TYPE"] = "filesystem"
app.config["SESSION_PERMANENT"] = False
Session(app)

@app.route("/", methods=["GET", "POST"])
def chat():
    if "history" not in session:
        session["history"] = []

    if request.method == "POST":
        user_input = request.form["user_input"]
        if user_input == '!reset':
            session["history"] = []
        openai.api_key = os.environ['OPENAI_API_KEY']

        system_prompt = {"role": "system", "content": "You are a snarky, yet helpful assistant. "}

        # Add user input to history
        session['history'].append({"role": "user", "content": user_input})
        
        response = openai.ChatCompletion.create(
            model="gpt-4",
            messages=system_prompt + session['history'],
        )
        
        bot_response = response.choices[0].message["content"]
        
        # Add bot response to history
        session['history'].append({"role": "GlovedBot", "content": bot_response})

        return render_template("index.html", conversation=session['history'])

    return render_template("index.html", conversation=session['history'])





if __name__ == "__main__":
    app.run(debug=True)