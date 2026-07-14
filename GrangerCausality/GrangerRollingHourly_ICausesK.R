library(httr)
library(jsonlite)
library(dplyr)
library(purrr)
library(vars)
library(roll)

setwd("~/Desktop/Model/Correlation/GrangerCausality")

data_dir <- "data_tech_all"
max_lag <- 5 # max lag (hours)
window <- 168 # trailing window size (overlapping hourly obs)
step <- 1      

tickers <- c(FTEC = "FTEC", 
             XLK = "XLK",
             XSD = "XSD", 
             QQQ = "QQQ", 
             AIQ = "AIQ", 
             ARTY = "ARTY",
             CHAT = "CHAT", 
             `S&P` = "^GSPC", 
             NDX = "^NDX")

## ---- 1. Yahoo Hourly index prices ----
fetch_yahoo_hourly <- function(sym) {
  url <- sprintf(
    "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=60m&range=730d",
    URLencode(sym, reserved = TRUE))
  
  r <- GET(url, user_agent("Mozilla/5.0"))
  stop_for_status(r)
  j <- fromJSON(content(r, as = "text", encoding = "UTF-8"))
  res <- j$chart$result
  tibble(epoch = res$timestamp[[1]],
         close = res$indicators$quote[[1]]$close[[1]]) |>
    filter(!is.na(close)) |>
    arrange(epoch) |>
    mutate(hour    = floor(epoch / 3600) * 3600,
           idx_ret = c(NA, diff(log(close)))) |>
    filter(is.finite(idx_ret)) |>
    distinct(hour, .keep_all = TRUE) |>
    dplyr::select(hour, idx_ret)
}

index_list <- imap(tickers, function(sym, nm) {
  message("Fetching", nm, " (", sym, ")")
  Sys.sleep(1)
  tryCatch(fetch_yahoo_hourly(sym), error = function(e) {
    warning(nm, "failed:", conditionMessage(e)); NULL
  })
}) |> compact()

## ---- 2. Kalshi markets ----
files <- list.files(data_dir, pattern = "_candlesticks\\.csv$",
                    recursive = TRUE, full.names = TRUE)

read_market <- function(f) {
  d <- read.csv(f, check.names = FALSE)
  g <- function(nm) if (nm %in% names(d)) suppressWarnings(as.numeric(d[[nm]])) else NA_real_
  price <- coalesce(g("price.close_dollars"), g("price.close"),
                    (coalesce(g("yes_bid.close_dollars"), g("yes_bid.close")) +
                     coalesce(g("yes_ask.close_dollars"), g("yes_ask.close"))) / 2)
  
  d |>
    mutate(price = price,
           hour  = floor(end_period_ts / 3600) * 3600) |>
    filter(!is.na(price)) |>
    arrange(hour) |>
    distinct(hour, .keep_all = TRUE) |>
    mutate(mkt_chg = c(NA, diff(price))) |>
    filter(is.finite(mkt_chg)) |>
    dplyr::select(hour, mkt_chg)
}

market_list <- set_names(
  map(files, ~ tryCatch(read_market(.x), error = function(e) NULL)),
  sub("_candlesticks\\.csv$", "", basename(files))
) |> compact()
market_list <- market_list[map_int(market_list, nrow) >= window]

message(length(market_list), " markets, ", length(index_list), " indices")

## ---- 3. Rolling Granger: index ----
# regressions (restricted: own lags; unrestricted: own + index lags)
roll_pair <- function(mkt, idx, mkt_name, idx_name) {
  m <- inner_join(mkt, idx, by = "hour") |> arrange(hour)
  n <- nrow(m)
  if (n < window + max_lag) return(NULL)
  if (sd(m$mkt_chg) == 0 || sd(m$idx_ret) == 0) return(NULL)

  # lag chosen once per pair on the full overlap
  sel <- tryCatch(VARselect(m[, c("mkt_chg", "idx_ret")], lag.max = max_lag,
                            type = "const"), error = function(e) NULL)
  p <- if (!is.null(sel)) sel$selection[["AIC(n)"]] else 1

  # lagged matrices
  y  <- m$mkt_chg[(p + 1):n]
  Xr <- sapply(seq_len(p), function(l) m$mkt_chg[(p + 1 - l):(n - l)])
  Xi <- sapply(seq_len(p), function(l) m$idx_ret[(p + 1 - l):(n - l)])

  rr <- roll_lm(Xr, matrix(y), width = window)$r.squared[, 1]
  ru <- roll_lm(cbind(Xr, Xi), matrix(y), width = window)$r.squared[, 1]

  df2 <- window - 2 * p - 1
  Fst <- pmax(((ru - rr) / p) / ((1 - ru) / df2), 0)
  pv  <- pf(Fst, p, df2, lower.tail = FALSE)

  # full-sample test for the FDR screen
  N    <- length(y)
  r2r  <- summary(lm(y ~ Xr))$r.squared
  r2u  <- summary(lm(y ~ cbind(Xr, Xi)))$r.squared
  Ffull  <- max(((r2u - r2r) / p) / ((1 - r2u) / (N - 2 * p - 1)), 0)
  p_full <- pf(Ffull, p, N - 2 * p - 1, lower.tail = FALSE)

  hours <- m$hour[(p + 1):n]
  keep  <- seq(window, length(y), by = step)
  keep  <- keep[is.finite(Fst[keep])]
  if (length(keep) == 0) return(NULL)

  tibble(market = mkt_name, index = idx_name,
         time = as.POSIXct(hours[keep], origin = "1970-01-01",
                           tz = "America/New_York"),
         lag_used = p,
         i_causes_k_p = pv[keep],
         i_causes_k_F = Fst[keep],
         full_sample_p = p_full)
}

results <- imap(market_list, function(mkt, mkt_name) {
  message("Rolling: ", mkt_name)
  imap(index_list, ~ roll_pair(mkt, .x, mkt_name, .y)) |> bind_rows()
}) |> bind_rows()

## ---- 4. Select pairs, then flag threshold crossings ----
# Two-stage screen before any alerting:
# BH-FDR across all pairs on the full-sample Granger p-value
# p < 0.01 SIG_ON VS p > 0.05 SIG_OFF
fdr_level <- 0.10
sig_frac <- 0.05

pair_screen <- results |>
  group_by(market, index) |>
  summarise(full_sample_p = first(full_sample_p),
            frac_sig      = mean(i_causes_k_p < 0.01),
            .groups = "drop") |>
  mutate(fdr_p    = p.adjust(full_sample_p, method = "BH"),
         selected = fdr_p < fdr_level & frac_sig >= sig_frac)

message(sum(pair_screen$selected), " of ", nrow(pair_screen),
        " pairs pass the FDR + fraction-of-time screen")
write.csv(pair_screen, "granger_pair_screen.csv", row.names = FALSE)

results <- results |>
  left_join(pair_screen |> dplyr::select(market, index, fdr_p, frac_sig, selected),
            by = c("market", "index")) |>
  mutate(sig = case_when(i_causes_k_p < 0.01 ~ "**",
                         i_causes_k_p < 0.05 ~ "*",
                         TRUE ~ "")) |>
  group_by(market, index) |>
  arrange(time, .by_group = TRUE) |>
  mutate(state = selected & coalesce(
           zoo::na.locf(case_when(i_causes_k_p < 0.01 ~ TRUE,
                                  i_causes_k_p > 0.05 ~ FALSE,
                                  TRUE ~ NA),
                        na.rm = FALSE), FALSE),
         event = case_when(
           state  & !coalesce(lag(state), FALSE) ~ "SIG_ON",
           !state &  coalesce(lag(state), FALSE) ~ "SIG_OFF",
           TRUE ~ "")) |>
  ungroup() |>
  dplyr::select(-state)

write.csv(results, "granger_rolling_hourly_i_causes_k.csv", row.names = FALSE)

# filtering alerted series (chronological)
alerts <- results |> filter(event != "") |> arrange(time)
write.csv(alerts, "granger_alerts.csv", row.names = FALSE)
message(nrow(results), " rows written; ", nrow(alerts), " threshold crossings")
View(alerts)
