from config import app_instance
import os

# token alive for how many seconds?
alive = int(os.getenv('tokentimetolive', 60))


# start the app with port 5000 and debug on!
if __name__ == '__main__':
    app_instance.run(host='0.0.0.0', port=5000, debug=True)
