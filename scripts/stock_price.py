import yfinance as yf

def run():
    ticker = yf.Ticker("AAPL")  # You can change this to another stock
    price = ticker.history(period="1d")['Close'].iloc[-1]
    print(f"Apple stock price is ${price:.2f}")
