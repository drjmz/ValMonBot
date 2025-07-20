# **Ethereum Validator & Node Monitor Bot**

This bot provides comprehensive, automated monitoring for your Ethereum staking setup. It is designed to run on your own server, connect to your own Ethereum nodes, and send detailed alerts directly to your Telegram chat.

## **Key Features**

* **Node Health & Automatic Failover:**  
  * Continuously monitors the health of your primary node by checking if its Consensus Layer (CL) and Execution Layer (EL) are in sync.  
  * If the primary node becomes unhealthy (out of sync or unreachable), it sends an immediate alert.  
  * It then **automatically fails over** to a pre-configured fallback node to ensure your validator monitoring is never interrupted.  
  * Sends a notification when a node recovers.  
* **Advanced Proposal Monitoring:**  
  * **Upcoming Alert:** Notifies you as soon as one of your validators is scheduled to propose a block.  
  * **Confirmation Alert:** After the proposal slot has passed, it confirms the outcome:  
    * **Success:** Sends a message with the total MEV rewards (in ETH) and the name of the relay used (e.g., bloxroute, flashbots).  
    * **Missed:** Sends an alert if the scheduled block was not proposed.  
* **Advanced Sync Committee Monitoring:**  
  * **Day-Ahead Assignment:** Notifies you as soon as a validator is assigned to an upcoming sync committee (\~27 hours in advance).  
  * **Upcoming Reminder:** Sends a reminder \~1.5 hours before the duty is set to begin.  
  * **End of Duty:** Sends a final notification when the sync committee duty period is complete.  
* **Validator Status:**  
  * Monitors your validators' status and sends an alert if any of them go offline.  
* **Remote Management via Telegram:**  
  * /logs: Fetches and sends the last 100 lines of the bot's log file for easy, on-the-go debugging.  
  * /confirm: Runs an on-demand health check of both nodes and all validators, returning a clean summary report to your chat.

## **Setup Instructions**

Follow these steps carefully to configure and run your bot.

### **Step 1: Create a Telegram Bot**

1. Open Telegram and search for the official **@BotFather**.  
2. Start a chat and send the /newbot command.  
3. Follow the prompts to give your bot a name (e.g., "My Staking Bot") and a unique username (e.g., MyStakingBot).  
4. BotFather will provide you with a **Bot Token**. Copy this token immediately and save it.

### **Step 2: Get Your Telegram Chat ID**

The bot needs to know where to send messages.

1. Find and start a chat with another bot called **@userinfobot**.  
2. For a **private chat**, send /start to @userinfobot, and it will reply with your user information, including your **Id**. This is your Chat ID.  
3. For a **group chat**, create a new group, add your bot as a member, then forward any message from your group to @userinfobot. It will reply with the group's **Id** (which will be a negative number).

### **Step 3: Prepare Your Project & Virtual Environment**

1. On the server where you will run the bot, create a new folder (e.g., eth-validator-bot).  
2. Save bot.py and requirements.txt inside this folder.  
3. Open a terminal, navigate into your project folder, and create a Python virtual environment:  
   python3 \-m venv venv

4. Activate the virtual environment. You must do this every time you open a new terminal to work on the project.  
   * On **Linux/macOS**: source venv/bin/activate  
   * On **Windows**: .\\venv\\Scripts\\activate  
5. Install the required dependencies:  
   pip install \-r requirements.txt

### **Step 4: Configure Your Nodes and Secrets**

1. In your project folder, create a new file named .env.  
2. Open the .env file and add the following content, replacing the placeholder values with your own information.  
   \# .env file

   \# \--- Telegram Configuration \---  
   TELEGRAM\_BOT\_TOKEN="YOUR\_TELEGRAM\_BOT\_TOKEN\_HERE"  
   TELEGRAM\_CHAT\_ID="YOUR\_CHAT\_ID\_HERE"

   \# \--- Primary Node Configuration (Required) \---  
   \# Your main node pair (e.g., Nethermind/Nimbus)  
   PRIMARY\_BEACON\_NODE\_URL="http://192.168.1.162:5052"  
   PRIMARY\_EXECUTION\_NODE\_URL="http://192.168.1.162:8545"

   \# \--- Fallback Node Configuration (Optional, but highly recommended) \---  
   \# Your backup node pair (e.g., Besu/Lighthouse)  
   FALLBACK\_BEACON\_NODE\_URL="http://192.168.1.110:5052"  
   FALLBACK\_EXECUTION\_NODE\_URL="http://192.168.1.110:8545"

   \# \--- Validator Configuration \---  
   \# A comma-separated list of your validator indices. NO SPACES after commas.  
   VALIDATOR\_INDICES="100,200,300,4242"

   \# \--- Optional Settings \---  
   \# How often the bot checks for updates, in seconds. Default is 12 (one slot).  
   CHECK\_INTERVAL\_SECONDS="12"

3. **Save the .env file.** Ensure your node API ports (e.g., 5052 for CL, 8545 for EL) are accessible from where you are running the bot.

### **Step 5: Run the Bot**

1. From your terminal (with the virtual environment activated), you can run the bot directly:  
   python3 bot.py

2. To run the bot as a persistent background process that will survive even if you close your terminal, use nohup:  
   nohup python3 bot.py \> nohup.out 2\>&1 &

The bot will start, connect to your nodes, and send a confirmation message to your Telegram chat. It will now run continuously, monitoring your entire staking operation.