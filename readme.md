# Take the Gamble Out of Gambling: Using Financial Data to Estimate Dynamic Fair Value in Prediction Markets

## Columbia Summer Undergraduate Research Experience in Mathematical Modelling (CSUREMM) 


### Authors:
[Manni Lin](https://github.com/mannilin) (<ml5226@barnard.edu>), [Solon Sun](https://github.com/ss7691-web) (<ss7691@columbia.edu>), [Lara Urteaga](https://github.com/lmu2107-create) (<lmu2107@columbia.edu>)
 

### Abstract 
​​This project aims to assess the information signalling between traditional financial markets and prediction markets, specifically estimating fair value prices that can be used to exploit edges on prediction markets. Existing literature primarily focuses on leveraging prediction markets to inform financial market strategies (Shamsi & Cuffe, 2021), based on the concept of “wisdom of crowds.” However, we choose to focus on the inverse relationship, and investigate whether traditional financial market data can inform trading strategies in prediction markets?  

The problem is explored through two pathways, single series allocation and multi-series allocation. Single series allocation positions with financial data time series forecast and optimizes sizes to maximize the expected Sharpe ratio of the combined directional-plus-contract return. This strategy focuses on allocating in  Kalshi series that predict the price levels of commodities and cryptocurrencies. The former is combined with an entropy penalty discouraging concentration. The multi-series allocation uses dynamic Granger causality to find indirect causality from the financial index towards the Science and Technology sector of Kalshi. To estimate the hourly fair value for each market in the portfolio, we apply the volume weighted quasibinomial GLM, to fit every Kalshi Market to financial index pairs. We then use this to create a dynamic portfolio and invest when the edge exceeds an entry threshold. 

## Project Structure

```
├── .github/        # GitHub actions, workflows, and issue templates
├── assets/         # Images, logos, and media files used in README
├── docs/           # Supplemental project documentation
├── src/            # Source code files
│   ├── components/ # Reusable UI components
│   ├── config/     # Configuration files and environment setups
│   └── main.js     # Main application entry point
├── tests/          # Unit, integration, and end-to-end tests
├── .gitignore      # Specifies intentionally untracked files to ignore
├── LICENSE         # Full text of the project license
├── package.json    # Project dependencies and lifecycle scripts
└── README.md       # This file
```
