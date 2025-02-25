from flask import Flask, flash, render_template, request, redirect, url_for, session, jsonify
import sqlite3
from datetime import datetime
import numpy as np
from sklearn.linear_model import LinearRegression

app = Flask(__name__)
app.secret_key = "your_secret_key"

# Initialize the database
def init_db():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    # Create users table (Already created)
    c.execute('''CREATE TABLE IF NOT EXISTS users (
                    user_id TEXT PRIMARY KEY,
                    name TEXT,
                    password TEXT)''')

    # Create collector data table (Already created)
    c.execute('''CREATE TABLE IF NOT EXISTS collector_data (
                    day INTEGER,
                    household_id TEXT,
                    food_waste REAL,
                    plastic_waste REAL,
                    paper_waste REAL,
                    food_segregation INTEGER,
                    plastic_segregation INTEGER,
                    paper_segregation INTEGER,
                    streak INTEGER,
                    collector_id TEXT,
                    colony TEXT,  
                    PRIMARY KEY (day, household_id))''')

    # Create customer table (Already created)
    c.execute('''CREATE TABLE IF NOT EXISTS customers (
                    community_name TEXT PRIMARY KEY,
                    name TEXT,
                    phone TEXT,
                    password TEXT)''')

    # Create collectors table to store approved collectors
    c.execute('''CREATE TABLE IF NOT EXISTS collectors (
                    user_id TEXT PRIMARY KEY,
                    FOREIGN KEY (user_id) REFERENCES users (user_id))''')
    
    # Table to store distributed rewards
    c.execute('''CREATE TABLE IF NOT EXISTS rewards (
                    household_id TEXT,
                    week_start TEXT,
                    week_end TEXT,
                    reward_amount REAL,
                    performance_level TEXT,
                    streak INTEGER,
                    PRIMARY KEY (household_id, week_start, week_end))''')

    conn.commit()
    conn.close()

def get_db_connection():
    conn = sqlite3.connect('database.db')
    conn.row_factory = sqlite3.Row  # Allows accessing columns by name
    return conn

# ------------------------
# Dummy AI/ML Model Training
# ------------------------
def train_reward_model():
    """
    Trains a dummy linear regression model using artificial data.
    Features: [total_waste, streak, negative_flag]
    Target: Unscaled reward score.
    
    For non-negative streaks, training data is as follows:
       High Performance (streak 6-7)   -> higher reward values
       Medium Performance (streak 3-5) -> moderate reward values
       Low Performance (streak 0-2)    -> lower reward values
       
    Negative streak cases are handled separately in the reward logic.
    """
    import numpy as np
    from sklearn.linear_model import LinearRegression

    # Dummy training data for households with non-negative streaks
    X = np.array([
        [10, 7, 0],   # High Performance example: high waste, streak 7 → reward 150
        [8, 6, 0],    # High Performance example: streak 6 → reward 130
        [5, 5, 0],    # Medium Performance example: streak 5 → reward 80
        [3, 3, 0],    # Medium Performance example: streak 3 → reward 60
        [2, 2, 0],    # Low Performance example: streak 2 → reward 30
        [1, 1, 0],    # Low Performance example: streak 1 → reward 20
        [0, 0, 1]     # Example with no waste (negative flag active) → reward 0
    ])
    y = np.array([150, 130, 80, 60, 30, 20, 0])
    model = LinearRegression()
    model.fit(X, y)
    return model

reward_model = train_reward_model()

# ------------------------
# Data Insertion Functions
# ------------------------

def insert_waste_entry(entry_date, food_waste, plastic_waste, paper_waste):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # Check if the date already exists
    cursor.execute("SELECT * FROM waste_entry WHERE entry_date = ?", (entry_date,))
    existing_entry = cursor.fetchone()

    if existing_entry:
        flash("⚠ Data for this date already exists! Please enter a different date.", "error")
        conn.close()
        return False  # Return False if duplicate

    # Convert entry_date to a weekday name (e.g., Monday, Tuesday, etc.)
    day_of_week = datetime.strptime(entry_date, "%Y-%m-%d").strftime("%A")  

    # Insert new entry
    cursor.execute("""
        INSERT INTO waste_entry (entry_date, day_of_week, food_waste, plastic_waste, paper_waste)
        VALUES (?, ?, ?, ?, ?)
    """, (entry_date, day_of_week, food_waste, plastic_waste, paper_waste))

    conn.commit()
    conn.close()
    flash("✅ Waste data submitted successfully!", "success")
    return True  # Return True if successful


def insert_waste_earnings(start_date, end_date, food_amount, plastic_amount, paper_amount):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # Check if the date range already exists
    cursor.execute("SELECT COUNT(*) FROM waste_earnings WHERE start_date = ? AND end_date = ?", (start_date, end_date))
    existing_count = cursor.fetchone()[0]

    if existing_count > 0:
        flash('This date range has already been used. Please select a different range.', 'error')
        conn.close()
        return False  # Prevent duplicate insertion

    # Insert new record
    cursor.execute("INSERT INTO waste_earnings (start_date, end_date, food_amount, plastic_amount, paper_amount) VALUES (?, ?, ?, ?, ?)",
                   (start_date, end_date, food_amount, plastic_amount, paper_amount))
    
    conn.commit()
    conn.close()
    flash("✅ Waste earnings added successfully!", "success")
    return True

# ------------------------
# AI/ML-Based Reward Distribution Logic
# ------------------------
def distribute_rewards_logic(start_date, end_date):
    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()
    
    # --- Fetch and convert earnings record ---
    cursor.execute("""
        SELECT * FROM waste_earnings
        WHERE start_date = ? AND end_date = ?
    """, (start_date, end_date))
    earnings_tuple = cursor.fetchone()
    if not earnings_tuple:
        conn.close()
        return False, "No waste earnings data found for the selected week."
    
    # Convert earnings tuple to a dictionary using cursor.description.
    earnings_columns = [desc[0] for desc in cursor.description]
    earnings = dict(zip(earnings_columns, earnings_tuple))
    
    total_weekly = earnings['food_amount'] + earnings['plastic_amount'] + earnings['paper_amount']
    reward_pool = 0.30 * total_weekly  # 30% of weekly earnings

    # --- Fetch and convert all collector_data records for the week ---
    cursor.execute("""
        SELECT * FROM collector_data
        WHERE entry_date BETWEEN ? AND ?
    """, (start_date, end_date))
    rows_tuple = cursor.fetchall()
    if not rows_tuple:
        conn.close()
        return False, "No collector data found for the selected week."
    
    # Convert each row tuple to a dictionary.
    collector_columns = [desc[0] for desc in cursor.description]
    rows = [dict(zip(collector_columns, row)) for row in rows_tuple]

    # --- Aggregate customer data ---
    customers = {}
    for row in rows:
        hid = row['household_id']
        if hid not in customers:
            customers[hid] = {
                'total_waste': 0.0,
                'non_zero_days': 0,
                'streak': None  # we will attempt to capture the day 7 streak if available
            }
        daily_waste = row['food_waste'] + row['plastic_waste'] + row['paper_waste']
        customers[hid]['total_waste'] += daily_waste
        if daily_waste > 0:
            customers[hid]['non_zero_days'] += 1
        
        # If this record is for day 7, capture the streak value.
        if row.get('day', None) == 7:
            customers[hid]['streak'] = row['streak']
    
    # For households missing a day 7 record, fallback to the maximum streak from available records.
    for hid, data in customers.items():
        if data['streak'] is None:
            relevant_rows = [row for row in rows if row['household_id'] == hid]
            data['streak'] = max(row['streak'] for row in relevant_rows)

    # Set negative_flag: if total_waste is zero, flag as 1; otherwise 0.
    for hid, data in customers.items():
        data['negative_flag'] = 1 if data['total_waste'] == 0 else 0

    # --- Predict rewards and assign performance levels ---
    predictions = {}
    performance_levels = {}
    positive_sum = 0.0

    for hid, data in customers.items():
        # If the streak is negative, apply a fixed penalty: -5 for each negative day.
        if data['streak'] < 0:
            predictions[hid] = -5 * abs(data['streak'])
            performance_levels[hid] = "Negative Streak"
        else:
            features = np.array([[data['total_waste'], data['streak'], data['negative_flag']]])
            pred = reward_model.predict(features)[0]
            # If the model prediction is negative, force a fixed penalty.
            if pred < 0:
                predictions[hid] = -5
            else:
                predictions[hid] = pred
                positive_sum += pred

            # Determine performance level based on the streak.
            if 6 <= data['streak'] <= 7:
                performance_levels[hid] = "High Performance"
            elif 3 <= data['streak'] <= 5:
                performance_levels[hid] = "Medium Performance"
            else:  # 0-2 days
                performance_levels[hid] = "Low Performance"
    
    # --- Scale positive predictions to the available reward pool ---
    final_rewards = {}
    for hid, pred in predictions.items():
        if pred >= 0:
            final_rewards[hid] = round(pred * (reward_pool / positive_sum), 2) if positive_sum > 0 else 0
        else:
            final_rewards[hid] = pred

    # --- Insert or update rewards records ---
    for hid, data in customers.items():
        cursor.execute("""
            INSERT OR REPLACE INTO rewards (household_id, week_start, week_end, reward_amount, performance_level, streak)
            VALUES (?, ?, ?, ?, ?, ?)
        """, (hid, start_date, end_date, final_rewards[hid], performance_levels[hid], data['streak']))
    
    conn.commit()
    conn.close()
    return True, "Rewards distributed successfully."

@app.route('/reward_calculation', methods=['GET', 'POST'])
def reward():
    if request.method == 'POST':
        print("Received Form Data:", request.form)  # Debugging Output

        # ✅ Handle Waste Data Entry Form Submission
        if 'submit_data' in request.form:
            entry_date = request.form['date']
            food_waste = float(request.form['food_waste'])
            plastic_waste = float(request.form['plastic_waste'])
            paper_waste = float(request.form['paper_waste'])

            print("Submitting Waste Entry: {entry_date}, {food_waste}, {plastic_waste}, {paper_waste}")  # Debugging

            if insert_waste_entry(entry_date, food_waste, plastic_waste, paper_waste):
                flash("✅ Data submitted successfully!", "success")
            return redirect(url_for('reward'))
  
        # ✅ Handle Waste Earnings Submission
        if 'submit_amounts' in request.form:
            if 'weekPicker' not in request.form or not request.form['weekPicker'].strip():
                flash("⚠ Week Picker value missing! Please select a valid date range.", "error")
                return redirect(url_for('reward'))

            date_range = request.form['weekPicker'].strip()
            print("Date Range Received:", date_range)  # Debugging

            if " to " in date_range:
                start_date, end_date = date_range.split(' to ')
            else:
                flash('❌ Invalid date range format.', 'error')
                return redirect(url_for('reward'))

            print("Parsed Start Date:", start_date)
            print("Parsed End Date:", end_date)

            food_amount = float(request.form['food_amount'])
            plastic_amount = float(request.form['plastic_amount'])
            paper_amount = float(request.form['paper_amount'])

            print("Amounts:", food_amount, plastic_amount, paper_amount)

            if insert_waste_earnings(start_date, end_date, food_amount, plastic_amount, paper_amount):
                flash('✅ Waste earnings added successfully!', 'success')

        # ✅ Handle Distribute Rewards Submission (from calendar selection)
        if 'distribute_rewards' in request.form:

            # Expecting the week range value in weekPicker (make sure your input element has a name attribute)
            if 'weekPicker' in request.form and request.form['weekPicker'].strip():
                date_range = request.form['weekPicker'].strip()
                print("Date Range from Distribute Rewards:", date_range)  # Debugging

                if " to " in date_range:
                    start_date, end_date = date_range.split(' to ')
                else:
                    flash("❌ Invalid date range format in distribute rewards.", "error")
                    return redirect(url_for('reward'))

                print("Parsed Start Date:", start_date)
                print("Parsed End Date:", end_date)

                success, message = distribute_rewards_logic(start_date, end_date)

                if success:
                    flash("✅ Rewards distributed successfully!", "success")
                else:
                    flash("❌ Rewards distributed unsuccessfully! ", "error")
            else:
                flash("⚠ No week range selected. Please select a valid week range.", "error")

            return redirect(url_for('reward'))
        
    return render_template('reward_calculation.html')

@app.route('/admin_dashboard', methods=['GET'])
def admin_dashboard():
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))

    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    # Fetch pending collectors (collectors who are not yet approved)
    c.execute("SELECT * FROM users WHERE user_id NOT IN (SELECT user_id FROM collectors)")
    pending_collectors = c.fetchall()

    conn.close()
    return render_template('admin_dashboard.html', pending_collectors=pending_collectors)

@app.route('/approve_collector/<string:user_id>', methods=['POST'])
def approve_collector(user_id):
    # 1. Connect to database
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    
    # 2. Move user from pending_collectors to collectors table (or do your logic)
    # Fetch the pending collector
    c.execute("SELECT user_id, name FROM pending_collectors WHERE user_id = ?", (user_id,))
    pending_user = c.fetchone()

    if pending_user:
        # Insert into 'collectors' or 'users' table
        c.execute("INSERT INTO collectors (user_id) VALUES (?)", (pending_user[0],))
        
        # Remove from 'pending_collectors'
        c.execute("DELETE FROM pending_collectors WHERE user_id = ?", (user_id,))
        
        conn.commit()
        flash(f"Collector '{pending_user[1]}' has been approved!", "success")
    else:
        flash("Collector not found or already approved.", "error")

    conn.close()
    # 3. Redirect back to the verification page
    return redirect(url_for('verification'))

@app.route('/reject_collector/<user_id>', methods=['POST'])
def reject_collector(user_id):
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))  # Redirect if not logged in

    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    # Remove from pending_collectors
    c.execute("DELETE FROM pending_collectors WHERE user_id = ?", (user_id,))
    conn.commit()
    conn.close()

    return redirect(url_for('verification'))  # Redirect back to verification page

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        user_id = request.form['user_id']
        password = request.form['password']

        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT * FROM users WHERE user_id = ? AND password = ?", (user_id, password))
        user = c.fetchone()

        if user:
            # Check if user is approved as a collector
            c.execute("SELECT * FROM collectors WHERE user_id = ?", (user_id,))
            collector = c.fetchone()

            if collector:  # If user is approved as a collector
                session['user_id'] = user[0]
                session['name'] = user[1]
                return redirect(url_for('data_entry'))
            else:
                return "You need to be approved as a collector to access this page."

        else:
            return "Invalid login credentials. Please try again."

    return render_template('login.html')

@app.route('/admin_login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        username = request.form['username']
        password = request.form['password']
        
        # Check if admin credentials are correct
        if username == "admin" and password == "admin123":
            session['admin_logged_in'] = True
            return redirect(url_for('admin_dashboard'))  # Redirect to admin dashboard
        
        flash("Invalid admin credentials!", "danger")  # Flash an error message
    
    return render_template('admin_login.html')  # Render admin login page

@app.route('/verification', methods=['GET'])
def verification():
    if 'admin_logged_in' not in session:
        return redirect(url_for('admin_login'))

    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    # ✅ Fetch pending collectors from `pending_collectors` table
    c.execute("SELECT user_id, name FROM pending_collectors")
    pending_collectors = c.fetchall()

    conn.close()

    return render_template('verification.html', pending_collectors=pending_collectors)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        user_id = request.form['user_id']
        name = request.form['name']
        password = request.form['password']

        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        # Check if user already exists
        c.execute("SELECT * FROM users WHERE user_id = ?", (user_id,))
        existing_user = c.fetchone()

        if existing_user:
            conn.close()
            return "User ID already exists. Please choose a different one."

        # Insert into 'users' table
        c.execute("INSERT INTO users (user_id, name, password) VALUES (?, ?, ?)", (user_id, name, password))

        # Also insert into 'pending_collectors' table
        c.execute("INSERT INTO pending_collectors (user_id, name) VALUES (?, ?)", (user_id, name))

        conn.commit()
        conn.close()

        return redirect(url_for('login'))  # Redirect to login after signup

    # ✅ If request method is 'GET', return the signup page
    return render_template('signup.html')  

@app.route('/customer_login', methods=['GET', 'POST'])
def customer_login():
    if request.method == 'POST':
        user_id = request.form['user_id']
        password = request.form['password']

        conn = sqlite3.connect('database.db')
        c = conn.cursor()
        c.execute("SELECT * FROM customers WHERE user_id = ? AND password = ?", (user_id, password))
        customer = c.fetchone()
        conn.close()

        if customer:
            # Store customer details in session
            session['customer_name'] = customer[3]  # Full Name
            session['household_id'] = customer[1]  # Household ID
            return redirect(url_for('customer_home'))  # Redirect to the home page
        else:
            return "Invalid User ID or Password. Please try again."

    return render_template('customer_login.html')

def generate_household_id():
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("SELECT MAX(SUBSTR(household_id, 2)) FROM customers")
    result = c.fetchone()
    max_id = int(result[0]) if result[0] else 0
    new_household_id = f"h{str(max_id + 1).zfill(3)}"  # Generates h001, h002, etc.

    conn.close()
    return new_household_id

@app.route('/customer_signup', methods=['GET', 'POST'])
def customer_signup():
    if request.method == 'POST':
        community_name = request.form['community_name']
        name = request.form['name']
        user_id = request.form['user_id']  # Include User ID field
        phone = request.form['phone']
        password = request.form['password']

        household_id = generate_household_id()  # Generate unique household ID

        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        # Check if phone number is exactly 10 digits
        if len(phone) != 10:
            conn.close()
            return "Invalid phone number. It must contain exactly 10 digits."

        # Check if user_id already exists
        c.execute("SELECT * FROM customers WHERE user_id = ?", (user_id,))
        existing_user = c.fetchone()
        if existing_user:
            conn.close()
            return "User ID already exists. Please choose a different one."

        # Check if phone number already exists
        c.execute("SELECT * FROM customers WHERE phone = ?", (phone,))
        existing_phone = c.fetchone()
        if existing_phone:
            conn.close()
            return "Phone number already exists. Please use a different phone number."

        # Insert new customer with generated household_id
        c.execute("""
            INSERT INTO customers (community_name, name, user_id, phone, password, household_id) 
            VALUES (?, ?, ?, ?, ?, ?)
        """, (community_name, name, user_id, phone, password, household_id))

        conn.commit()
        conn.close()

        return redirect(url_for('customer_login'))  # Redirect to login page

    return render_template('customer_signup.html')

@app.route('/customer_home')
def customer_home():
    if 'customer_name' not in session:
        return redirect(url_for('customer_login'))

    return render_template('customer_home.html')

@app.route('/logout')
def logout():
    session.clear()
    return redirect(url_for('welcome'))

@app.route('/history', methods=['GET'])
def history():
    if 'user_id' not in session:
        return redirect(url_for('login'))  # Redirect to login if not logged in

    search_date = request.args.get('search_date')
    collector_id = session['user_id']  # Now it's safe to access

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    # If search_date is provided, filter by that date
    if search_date:
        cursor.execute("""
            SELECT cd.entry_date, cd.day, cd.household_id, cd.food_waste, cd.plastic_waste, cd.paper_waste, 
                   cd.food_segregation, cd.plastic_segregation, cd.paper_segregation, 
                   cd.streak, u.name AS collector_name
            FROM collector_data cd
            JOIN users u ON cd.collector_id = u.user_id
            WHERE cd.collector_id = ? AND cd.entry_date = ?
        """, (collector_id, search_date))
    else:
        cursor.execute("""
            SELECT cd.entry_date, cd.day, cd.household_id, cd.food_waste, cd.plastic_waste, cd.paper_waste, 
                   cd.food_segregation, cd.plastic_segregation, cd.paper_segregation, 
                   cd.streak, u.name AS collector_name
            FROM collector_data cd
            JOIN users u ON cd.collector_id = u.user_id
            WHERE cd.collector_id = ?
        """, (collector_id,))

    history_data = cursor.fetchall()
    conn.close()

    # Format entry_date in mm/dd/yyyy if it's not None
    for i, row in enumerate(history_data):
        entry_date = row[0]
        if entry_date:
            formatted_date = f"{entry_date[5:7]}/{entry_date[8:10]}/{entry_date[:4]}"  # mm/dd/yyyy
            history_data[i] = row[:0] + (formatted_date,) + row[1:]
        else:
            history_data[i] = row[:0] + ("N/A",) + row[1:]

    return render_template('history.html', history_data=history_data)

@app.route('/customer_history', methods=['GET'])
def customer_history():
    if 'household_id' not in session:
        return "Unauthorized access", 403  # Ensure only logged-in customers can access

    household_id = session['household_id']
    search_date = request.args.get('search_date')

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    household_id = str(household_id)  # Ensure household_id is a string

    if search_date:
        print(f"Searching for household_id: {household_id} on date: {search_date}")  # Debugging output

        cursor.execute("""
            SELECT entry_date, day, household_id, food_waste, plastic_waste, paper_waste, 
                   food_segregation, plastic_segregation, paper_segregation, 
                   streak, collector_id, colony
            FROM collector_data
            WHERE household_id = ? AND entry_date = ?
        """, (household_id, search_date))
    else:
        cursor.execute("""
            SELECT entry_date, day, household_id, food_waste, plastic_waste, paper_waste, 
                   food_segregation, plastic_segregation, paper_segregation, 
                   streak, collector_id, colony
            FROM collector_data
            WHERE household_id = ?
        """, (household_id,))

    history_data = cursor.fetchall()
    conn.close()

    # Debugging: Print fetched data in the console
    print(f"Fetched History for {household_id}: {history_data}")

    return render_template('customer_history.html', history_data=history_data)

@app.route('/admin_history', methods=['GET', 'POST'])
def admin_history():
    # Ensure correct session key for admin login
    if 'admin_logged_in' not in session or not session['admin_logged_in']:
        return redirect(url_for('admin_login'))  # Redirects only to correct admin login

    conn = sqlite3.connect('database.db')
    c = conn.cursor()

    # Fetch distinct colonies and collector IDs for filtering
    c.execute("SELECT DISTINCT colony FROM collector_data")
    colonies = [row[0] for row in c.fetchall()]

    c.execute("SELECT DISTINCT collector_id FROM collector_data")
    collectors = [row[0] for row in c.fetchall()]

    data = []
    
    # Default query to fetch all records
    query = """SELECT entry_date, day, household_id, food_waste, plastic_waste, paper_waste, 
                      food_segregation, plastic_segregation, paper_segregation, 
                      streak, collector_id, colony 
               FROM collector_data WHERE 1=1"""
    params = []

    # Check if filters are applied
    if request.method == 'POST':
        date = request.form.get('date')
        collector_id = request.form.get('collector_id')
        colony = request.form.get('colony')

        if date:
            query += " AND entry_date = ?"
            params.append(date)
        if collector_id:
            query += " AND collector_id = ?"
            params.append(collector_id)
        if colony:
            query += " AND colony = ?"
            params.append(colony)

    c.execute(query, params)
    data = c.fetchall()
    conn.close()

    return render_template('admin_history.html', data=data, colonies=colonies, collectors=collectors)

@app.route('/admin/statistics')
def admin_statistics():
    if 'admin_logged_in' not in session or not session['admin_logged_in']:
        return redirect(url_for('admin_login'))

    return render_template('admin_statistics.html')

@app.route('/get_statistics', methods=['POST'])
def get_statistics():
    data = request.json
    start_date = data.get("start_date")  
    end_date = data.get("end_date")  
    colony = data.get("colony", "").strip().lower()  # Convert to lowercase and remove spaces

    conn = sqlite3.connect('database.db')
    cursor = conn.cursor()

    if colony and colony != "all":  
        cursor.execute("""
            SELECT SUM(food_waste), SUM(plastic_waste), SUM(paper_waste) 
            FROM collector_data 
            WHERE entry_date BETWEEN ? AND ? AND LOWER(colony) = ?
        """, (start_date, end_date, colony))
    else:
        cursor.execute("""
            SELECT SUM(food_waste), SUM(plastic_waste), SUM(paper_waste) 
            FROM collector_data 
            WHERE entry_date BETWEEN ? AND ?
        """, (start_date, end_date))

    result = cursor.fetchone()
    conn.close()

    total_food_waste = result[0] if result[0] else 0
    total_plastic_waste = result[1] if result[1] else 0
    total_paper_waste = result[2] if result[2] else 0
    total_waste = total_food_waste + total_plastic_waste + total_paper_waste  

    return jsonify({
        "food_waste": total_food_waste,
        "plastic_waste": total_plastic_waste,
        "paper_waste": total_paper_waste,
        "total_waste": total_waste
    })

@app.route('/customer/rewards')
def customer_rewards():
    return render_template('customer_rewards.html')

@app.route('/customer/help')
def customer_help():
    return render_template('customer_help.html')

@app.route('/data_entry', methods=['GET', 'POST'])
def data_entry():
    if 'user_id' not in session:
        return redirect(url_for('login'))

    if request.method == 'POST':
        day = int(request.form['day'])  # Collectors enter 1-7 (Monday-Sunday)
        household_id = request.form['household_id']
        food_waste = float(request.form['food_waste'])
        plastic_waste = float(request.form['plastic_waste'])
        paper_waste = float(request.form['paper_waste'])
        food_segregation = int(request.form['food_segregation'])
        plastic_segregation = int(request.form['plastic_segregation'])
        paper_segregation = int(request.form['paper_segregation'])
        colony = request.form['colony']
        entry_date = request.form['entry_date']  # Date from the form (yyyy-mm-dd format)

        collector_id = session['user_id']

        conn = sqlite3.connect('database.db')
        c = conn.cursor()

        # Fetch last recorded day and streak for this household
        c.execute("SELECT day, streak FROM collector_data WHERE household_id = ? ORDER BY day DESC LIMIT 1", (household_id,))
        result = c.fetchone()
        last_day = result[0] if result else None
        last_streak = result[1] if result else 0

        # Determine new streak
        if last_day is not None:
            if day == 1 and last_day == 7:  # New week starts (Monday → Sunday)
                streak = 1
            elif day == last_day + 1:  # Consecutive day (e.g., Monday -> Tuesday)
                if food_segregation == 0 or plastic_segregation == 0 or paper_segregation == 0:
                    streak = last_streak - 1  # Reduce streak for improper segregation
                else:
                    streak = last_streak + 1  # Increase streak for proper segregation
            else:  # Non-consecutive or incorrect day order → reset streak
                streak = 1
        else:
            streak = 1  # First-time entry

        # Ensure streak reflects negative values correctly
        if food_segregation == 0 or plastic_segregation == 0 or paper_segregation == 0:
            streak = last_streak - 1  # Deduct consistently for continuous improper segregation

        # Insert data into the database
        c.execute('''INSERT INTO collector_data (entry_date, day, household_id, food_waste, plastic_waste, paper_waste,
                                                 food_segregation, plastic_segregation, paper_segregation, streak, collector_id, colony)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''',
                  (entry_date, day, household_id, food_waste, plastic_waste, paper_waste, food_segregation, plastic_segregation,
                   paper_segregation, streak, collector_id, colony))
        conn.commit()
        conn.close()

        return redirect(url_for('data_entry'))

    # Fetch data sorted by household_id
    conn = sqlite3.connect('database.db')
    c = conn.cursor()
    c.execute("""
    SELECT * FROM collector_data 
    ORDER BY DATE(entry_date) ASC, 
             day ASC, 
             CAST(SUBSTR(household_id, 2) AS INTEGER) ASC;
    """)

    data = c.fetchall()
    conn.close()

    print("Sorted Data Sent to HTML:", data)  # Debugging step ✅
    
    return render_template('data_entry.html', data=data)  # Make sure the correct data is sent

@app.route('/')
def welcome():
    return render_template('welcome.html')

if __name__ == "__main__":
    init_db()
    app.run(debug=True)




