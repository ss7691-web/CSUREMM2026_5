library(httr)
library(jsonlite)
library(dplyr)
library(tidyr)
library(ggplot2)
library(patchwork)

BASE_DIR <- "~/Desktop/Model/stage2/fairvalueDynamic"
KALSHI_DIR <- file.path(BASE_DIR, "data_tech_all")
ALERTS_CSV <- file.path(BASE_DIR, "granger_alerts.csv")
OUTPUT_DIR <- file.path(BASE_DIR, "fair_value_outputs_dynamic")
PLOT_DIR <- file.path(OUTPUT_DIR, "plots")
dir.create(OUTPUT_DIR, showWarnings = FALSE)
dir.create(PLOT_DIR,   showWarnings = FALSE)

MIN_OBS <- 50      
CI_LEVEL <- 0.90
CI_Z <- qnorm(1 - (1 - CI_LEVEL) / 2)
MAX_CI_HALF_WIDTH <- 0.22
MAX_CUM_SE2 <- (MAX_CI_HALF_WIDTH / CI_Z)^2
N_REL_BINS <- 8
OVERFIT_R2 <- 0.999
SEED_WINDOW_H <- 72  
RECAL_MIN_OBS <- 40     
ANCHOR_DECAY_TAU_H <- 24    
RUG_DENSITY_THRESHOLD <- 0.60

## ---- Pairs from the rolling Granger screen ----
alerts <- read.csv(ALERTS_CSV, stringsAsFactors = FALSE) %>%
  dplyr::mutate(time = as.POSIXct(time, tz = "America/New_York"))

pairs <- alerts %>%
  dplyr::filter(selected == TRUE | selected == "TRUE") %>%
  dplyr::distinct(market, index, lag_used, full_sample_p, fdr_p, frac_sig) %>%
  dplyr::mutate(lag_used = as.integer(lag_used))

cat(sprintf("Selected pairs: %d  (%d markets, %d indices)\n",
            nrow(pairs), dplyr::n_distinct(pairs$market),
            dplyr::n_distinct(pairs$index)))

## Signal windows per pair
data_end <- max(alerts$time)
signal_windows <- alerts %>%
  dplyr::filter(event %in% c("SIG_ON", "SIG_OFF")) %>%
  dplyr::group_by(market, index) %>%
  dplyr::arrange(time, .by_group = TRUE) %>%
  dplyr::mutate(on_id = cumsum(event == "SIG_ON")) %>%
  dplyr::group_by(market, index, on_id) %>%
  dplyr::summarise(w_start = time[event == "SIG_ON"][1],
                   w_end   = if (any(event == "SIG_OFF"))
                               time[event == "SIG_OFF"][1] else data_end,
                   .groups = "drop")

flag_signal_on <- function(mkt, idx, datetimes) {
  w <- signal_windows %>% dplyr::filter(market == mkt, index == idx)
  if (nrow(w) == 0) return(rep(FALSE, length(datetimes)))
  sapply(datetimes, function(d) any(d >= w$w_start & d <= w$w_end))
}

## ---- Hourly index data from Yahoo ----
YAHOO_MAP <- c(FTEC = "FTEC", 
               XLK = "XLK",
               XSD = "XSD", 
               QQQ = "QQQ", 
               AIQ = "AIQ", 
               ARTY = "ARTY",
               CHAT = "CHAT", 
               `S&P` = "^GSPC", 
               NDX = "^NDX")

fetch_yahoo_hourly <- function(idx_name) {
  sym <- YAHOO_MAP[idx_name]
  if (is.na(sym)) { warning(sprintf("No Yahoo symbol for '%s'", idx_name)); return(NULL) }
  url <- sprintf(
    "https://query1.finance.yahoo.com/v8/finance/chart/%s?interval=60m&range=730d",
    URLencode(sym, reserved = TRUE))
  r <- GET(url, user_agent("Mozilla/5.0"))
  stop_for_status(r)
  j <- fromJSON(content(r, as = "text", encoding = "UTF-8"))
  res <- j$chart$result
  tibble(epoch = res$timestamp[[1]],
         ETF_close = res$indicators$quote[[1]]$close[[1]]) %>%
    dplyr::filter(!is.na(ETF_close)) %>%
    dplyr::arrange(epoch) %>%
    dplyr::mutate(hour = floor(epoch / 3600) * 3600,
                  ETF_ret = c(NA, diff(log(ETF_close)))) %>%
    dplyr::filter(is.finite(ETF_ret)) %>%
    dplyr::distinct(hour, .keep_all = TRUE) %>%
    dplyr::select(hour, ETF_close, ETF_ret)
}

index_cache <- list()
load_index <- function(idx_name) {
  if (!is.null(index_cache[[idx_name]])) return(index_cache[[idx_name]])
  cat(sprintf("  Fetching %s from Yahoo...\n", idx_name))
  Sys.sleep(1)
  df <- tryCatch(fetch_yahoo_hourly(idx_name), error = function(e) {
    warning(sprintf("%s failed: %s", idx_name, conditionMessage(e))); NULL })
  index_cache[[idx_name]] <<- df
  df
}

## ---- Candlestick files ----
all_kal_files <- list.files(KALSHI_DIR, pattern = "_candlesticks\\.csv$",
                            recursive = TRUE, full.names = TRUE)
kal_meta <- data.frame(
  fpath  = all_kal_files,
  market = sub("_candlesticks\\.csv$", "", basename(all_kal_files)),
  stringsAsFactors = FALSE)

meta_files <- list.files(KALSHI_DIR, pattern = "_markets_(historical|live)\\.csv$",
                         recursive = TRUE, full.names = TRUE)
close_times <- dplyr::bind_rows(lapply(meta_files, function(f)
    tryCatch({
      d <- read.csv(f, stringsAsFactors = FALSE)
      if (nrow(d) == 0) return(NULL)
      data.frame(ticker     = as.character(d$ticker),
                 close_time = as.character(d$close_time),
                 stringsAsFactors = FALSE)
    }, error = function(e) NULL))) %>%
  dplyr::mutate(expiry = as.POSIXct(close_time, format = "%Y-%m-%dT%H:%M:%SZ",
                                    tz = "UTC")) %>%
  dplyr::filter(!is.na(expiry)) %>%
  dplyr::group_by(ticker) %>%
  dplyr::summarise(expiry = max(expiry), .groups = "drop")

parse_expiry_fallback <- function(stem) {
  parts <- strsplit(stem, "-")[[1]]
  tok <- parts[grepl("^\\d{2}", parts)][1]
  if (is.na(tok)) return(as.POSIXct(NA))
  yr <- as.integer(substr(tok, 1, 2)) + 2000
  mm <- match(substr(tok, 3, 5), toupper(month.abb))
  dd <- suppressWarnings(as.integer(substr(tok, 6, 7)))
  d <- if (!is.na(mm) && !is.na(dd)) sprintf("%d-%02d-%02d", yr, mm, dd)
       else if (!is.na(mm)) format(as.Date(sprintf("%d-%02d-01", yr, mm)) + 30)
       else sprintf("%d-12-31", yr)
  as.POSIXct(paste(d, "23:59:59"), tz = "UTC")
}

get_expiry <- function(mkt) {
  hit <- close_times$expiry[close_times$ticker == mkt]
  if (length(hit) >= 1 && !is.na(hit[1])) return(hit[1])
  parse_expiry_fallback(mkt)
}

## ---- Kalshi loaders ----
pick_cols <- function(df) {
  if ("yes_ask.close_dollars" %in% colnames(df))
    list(ask = "yes_ask.close_dollars", bid = "yes_bid.close_dollars",
         vol = if ("volume_fp" %in% colnames(df)) "volume_fp" else NULL)
  else if ("yes_ask.close" %in% colnames(df))
    list(ask = "yes_ask.close", bid = "yes_bid.close",
         vol = if ("volume" %in% colnames(df)) "volume" else NULL)
  else NULL
}

load_kalshi <- function(fpath, expiry) {
  df <- read.csv(fpath, stringsAsFactors = FALSE)
  cc <- pick_cols(df)
  if (is.null(cc)) { warning(sprintf("Unknown schema: %s", basename(fpath))); return(NULL) }
  df %>%
    dplyr::mutate(
      hour  = floor(end_period_ts / 3600) * 3600,
      K_ask = .data[[cc$ask]], K_bid = .data[[cc$bid]],
      vol   = if (!is.null(cc$vol)) .data[[cc$vol]] else 1) %>%
    dplyr::arrange(hour) %>%
    dplyr::distinct(hour, .keep_all = TRUE) %>%
    dplyr::filter(K_bid > 0) %>%
    dplyr::mutate(
      K_mid = (K_ask + K_bid) / 2,
      K_mid_clamped = pmax(1e-4, pmin(1 - 1e-4, K_mid)),
      logit_K = log(K_mid_clamped / (1 - K_mid_clamped)),
      logit_K_lag1 = dplyr::lag(logit_K, 1),
      TTE_h = as.numeric(expiry) - hour,   # seconds
      TTE_h = TTE_h / 3600,                # hours TTE
      log_TTE = ifelse(TTE_h > 0, log(TTE_h), NA_real_),
      vol_weight = log1p(vol)) %>%
    dplyr::select(hour, K_mid, K_mid_clamped, logit_K, logit_K_lag1,
                  log_TTE, vol_weight, vol)
}

load_kalshi_actuals <- function(fpath) {
  df <- read.csv(fpath, stringsAsFactors = FALSE)
  cc <- pick_cols(df)
  if (is.null(cc)) return(NULL)
  df %>%
    dplyr::mutate(hour = floor(end_period_ts / 3600) * 3600,
                  K_ask = .data[[cc$ask]], K_bid = .data[[cc$bid]]) %>%
    dplyr::arrange(hour) %>%
    dplyr::distinct(hour, .keep_all = TRUE) %>%
    dplyr::filter(K_bid > 0) %>%
    dplyr::mutate(K_mid_actual = (K_ask + K_bid) / 2) %>%
    dplyr::select(hour, K_mid_actual)
}

## ---- Helpers ----
reliability_diagram <- function(pred, obs, n_bins = N_REL_BINS, title = "Reliability") {
  df <- data.frame(pred = pred, obs = obs) %>%
    dplyr::filter(!is.na(pred), !is.na(obs)) %>%
    dplyr::arrange(pred) %>%
    dplyr::mutate(bin = dplyr::ntile(pred, n_bins))
  if (nrow(df) < n_bins * 2) return(NULL)
  bin_df <- df %>%
    dplyr::group_by(bin) %>%
    dplyr::summarise(mean_pred = mean(pred), mean_obs = mean(obs),
                     n = dplyr::n(), .groups = "drop")
  ggplot(bin_df, aes(x = mean_pred, y = mean_obs)) +
    geom_abline(slope = 1, intercept = 0, linetype = "dashed",
                colour = "grey70", linewidth = 0.6) +
    geom_line(colour = "#2a78d6", linewidth = 0.8) +
    geom_point(aes(size = n), colour = "#2a78d6", alpha = 0.8) +
    scale_size_continuous(range = c(1.5, 5), guide = "none") +
    scale_x_continuous(limits = c(0, 1), labels = scales::percent_format(accuracy = 1)) +
    scale_y_continuous(limits = c(0, 1), labels = scales::percent_format(accuracy = 1)) +
    labs(x = "Mean predicted", y = "Observed frequency", title = title) +
    theme_minimal(base_size = 10) + theme(panel.grid.minor = element_blank())
}

get_seed <- function(kal_actuals, first_pred_hour, fallback_logit) {
  if (is.null(kal_actuals) || nrow(kal_actuals) == 0) return(fallback_logit)
  candidates <- kal_actuals %>%
    dplyr::filter(hour >= first_pred_hour - SEED_WINDOW_H * 3600,
                  hour <= first_pred_hour + 2 * 3600) %>%
    dplyr::arrange(hour)
  if (nrow(candidates) == 0) return(fallback_logit)
  p <- pmax(1e-4, pmin(1 - 1e-4, candidates$K_mid_actual[1]))
  log(p / (1 - p))
}

platt_recalibrate <- function(pred_shrunk, actual) {
  df <- data.frame(y = actual,
                   logp = qlogis(pmax(1e-4, pmin(1 - 1e-4, pred_shrunk)))) %>%
    dplyr::filter(!is.na(y), !is.na(logp))
  if (nrow(df) < RECAL_MIN_OBS) return(NULL)
  tryCatch(glm(y ~ logp, data = df, family = binomial()), error = function(e) NULL)
}

apply_recal <- function(fit_cal, pred_shrunk) {
  if (is.null(fit_cal)) return(rep(NA_real_, length(pred_shrunk)))
  logp <- qlogis(pmax(1e-4, pmin(1 - 1e-4, pred_shrunk)))
  plogis(coef(fit_cal)[1] + coef(fit_cal)[2] * logp)
}

## ---- Main Loop ----
results_log <- list()
market_outputs <- list()


for (i in seq_len(nrow(pairs))) {

  mkt   <- pairs$market[i]
  idx   <- pairs$index[i]
  lag_k <- pairs$lag_used[i]
  gp    <- pairs$full_sample_p[i]

  cat(sprintf("\n─── [%d/%d] %s × %s  (lag=%dh, full-sample p=%.4f, fdr p=%.4f)\n",
              i, nrow(pairs), mkt, idx, lag_k, gp, pairs$fdr_p[i]))

  fpath <- kal_meta$fpath[kal_meta$market == mkt]
  if (length(fpath) == 0) { cat("  SKIP: no candlestick file\n"); next }
  fpath  <- fpath[1]
  expiry <- get_expiry(mkt)
  cat(sprintf("  expiry: %s UTC\n", format(expiry, "%Y-%m-%d %H:%M")))

  kal_df <- load_kalshi(fpath, expiry)
  if (is.null(kal_df) || nrow(kal_df) == 0) { cat("  SKIP: kalshi load failed\n"); next }

  etf_df <- load_index(idx)
  if (is.null(etf_df)) { cat(sprintf("  SKIP: index '%s' unavailable\n", idx)); next }

  lag_cols <- paste0("ETF_ret_L", seq_len(lag_k))
  for (lk in seq_len(lag_k))
    etf_df[[paste0("ETF_ret_L", lk)]] <- dplyr::lag(etf_df$ETF_ret, lk)

  reg_df <- kal_df %>%
    dplyr::inner_join(etf_df, by = "hour") %>%
    tidyr::drop_na(logit_K_lag1, ETF_ret, log_TTE, vol_weight,
                   dplyr::all_of(lag_cols))

  n_obs <- nrow(reg_df)
  cat(sprintf("  Fit sample: %d hourly obs\n", n_obs))
  if (n_obs < MIN_OBS) {
    cat(sprintf("  SKIP: %d obs < %d\n", n_obs, MIN_OBS))
    results_log[[length(results_log) + 1]] <- data.frame(
      market = mkt, etf = idx, status = "SKIPPED_LOW_OBS", n_obs = n_obs)
    next
  }

  model_formula <- as.formula(paste0(
    "K_mid_clamped ~ log_TTE + logit_K_lag1 + ETF_ret + ",
    paste(lag_cols, collapse = " + ")))

  fit <- tryCatch(
    glm(model_formula, data = reg_df,
        family = quasibinomial(link = "logit"), weights = vol_weight),
    error = function(e) { cat(sprintf("  GLM ERROR: %s\n", e$message)); NULL })
  if (is.null(fit)) {
    results_log[[length(results_log) + 1]] <- data.frame(
      market = mkt, etf = idx, status = "GLM_ERROR", n_obs = n_obs)
    next
  }

  pseudo_r2   <- 1 - fit$deviance / fit$null.deviance
  rmse_val    <- sqrt(mean((reg_df$K_mid - fitted(fit))^2))
  is_overfit  <- pseudo_r2 >= OVERFIT_R2
  shrink_mult <- if (is_overfit) (1 - gp) * 0.50 else (1 - gp)
  cat(sprintf("  R²=%.3f | RMSE=%.4f | overfit=%s | shrink=%.3f\n",
              pseudo_r2, rmse_val, is_overfit, shrink_mult))

  kal_actuals <- load_kalshi_actuals(fpath)
  hour_min <- min(kal_actuals$hour); hour_max <- max(kal_actuals$hour)
  fallback_logit <- tail(reg_df$logit_K, 1)

  ## Prediction grid: every index trading hour inside the market's life
  pred_df <- etf_df %>%
    dplyr::filter(hour >= hour_min, hour <= hour_max) %>%
    tidyr::drop_na(ETF_ret, dplyr::all_of(lag_cols)) %>%
    dplyr::arrange(hour) %>%
    dplyr::mutate(TTE_h   = (as.numeric(expiry) - hour) / 3600,
                  log_TTE = ifelse(TTE_h > 0, log(TTE_h), NA_real_)) %>%
    tidyr::drop_na(log_TTE) %>%
    dplyr::left_join(kal_actuals, by = "hour") %>%
    dplyr::mutate(datetime = as.POSIXct(hour, origin = "1970-01-01",
                                        tz = "America/New_York"))

  if (nrow(pred_df) == 0) { cat("  SKIP: no index hours in window\n"); next }

  seed_logit <- get_seed(kal_actuals, pred_df$hour[1], fallback_logit)

  ## Walk forward AR(1) with anchoring & decay
  pred_df$logit_K_lag1 <- NA_real_
  pred_df$fair_value_prob <- NA_real_
  pred_df$fv_ci_lo <- NA_real_
  pred_df$fv_ci_hi <- NA_real_
  pred_df$anchored <- FALSE
  pred_df$hours_since_anchor <- NA_integer_

  prev_logit         <- seed_logit
  last_anchor_logit  <- seed_logit
  hours_since_anchor <- 0L
  cum_se2            <- 0

  for (k in seq_len(nrow(pred_df))) {
    pred_df$logit_K_lag1[k] <- prev_logit
    pred_df$hours_since_anchor[k] <- hours_since_anchor

    pout <- tryCatch(
      predict(fit, newdata = pred_df[k, , drop = FALSE],
              type = "link", se.fit = TRUE),
      error = function(e) list(fit = NA_real_, se.fit = NA_real_))
    eta    <- pout$fit
    se_eta <- if (is.numeric(pout$se.fit)) pout$se.fit else NA_real_
    pred_p <- if (!is.na(eta)) plogis(eta) else NA_real_
    pred_df$fair_value_prob[k] <- pred_p

    if (!is.na(se_eta)) {
      cum_se2  <- min(cum_se2 + se_eta^2, MAX_CUM_SE2)
      total_se <- sqrt(cum_se2)
      pred_df$fv_ci_lo[k] <- plogis(eta - CI_Z * total_se)
      pred_df$fv_ci_hi[k] <- plogis(eta + CI_Z * total_se)
    }

    if (!is.na(pred_df$K_mid_actual[k])) {
      actual_p <- pmax(1e-4, pmin(1 - 1e-4, pred_df$K_mid_actual[k]))
      last_anchor_logit  <- log(actual_p / (1 - actual_p))
      hours_since_anchor <- 0L
      prev_logit <- last_anchor_logit
      cum_se2 <- if (!is.na(se_eta)) se_eta^2 else 0
      pred_df$anchored[k] <- TRUE
    } else if (!is.na(pred_p) && pred_p > 0 && pred_p < 1) {
      model_logit <- log(pred_p / (1 - pred_p))
      decay_weight <- exp(-hours_since_anchor / ANCHOR_DECAY_TAU_H)
      prev_logit <- decay_weight * last_anchor_logit +
                      (1 - decay_weight) * model_logit
      hours_since_anchor <- hours_since_anchor + 1L
    }
  }

  pred_df <- pred_df %>%
    dplyr::mutate(
      fair_value_shrunk = 0.5 + (fair_value_prob - 0.5) * shrink_mult,
      fv_ci_lo_shrunk   = 0.5 + (fv_ci_lo - 0.5) * shrink_mult,
      fv_ci_hi_shrunk   = 0.5 + (fv_ci_hi - 0.5) * shrink_mult,
      signal_on         = flag_signal_on(mkt, idx, datetime),
      market = mkt, etf = idx,
      model_RMSE = rmse_val, model_pseudoR2 = pseudo_r2, n_obs = n_obs,
      granger_p = gp, fdr_p = pairs$fdr_p[i], frac_sig = pairs$frac_sig[i],
      lag_used = lag_k, is_overfit = is_overfit, shrink_mult = shrink_mult)

  ## Point-in-time Platt Recalibration
  pred_df$fair_value_recal <- NA_real_
  for (k in seq_len(nrow(pred_df))) {
    if (k > 1) {
      past_eval <- pred_df[seq_len(k - 1), ] %>%
        dplyr::filter(!is.na(fair_value_shrunk), !is.na(K_mid_actual))
      if (nrow(past_eval) >= RECAL_MIN_OBS) {
        rc <- platt_recalibrate(past_eval$fair_value_shrunk, past_eval$K_mid_actual)
        pred_df$fair_value_recal[k] <- apply_recal(rc, pred_df$fair_value_shrunk[k])
      }
    }
  }
  used_recal <- any(!is.na(pred_df$fair_value_recal))
  pred_df <- pred_df %>%
    dplyr::mutate(fair_value_final = dplyr::if_else(
      !is.na(fair_value_recal), fair_value_recal, fair_value_shrunk))

  ## Metrics
  eval_df <- pred_df %>% dplyr::filter(!is.na(fair_value_final), !is.na(K_mid_actual))
  has_actual <- nrow(eval_df) >= 2
  skill_final <- NA_real_
  if (has_actual) {
    err <- eval_df$fair_value_final - eval_df$K_mid_actual
    skill_final <- 1 - mean(err^2) / mean((0.5 - eval_df$K_mid_actual)^2)
  }
  neg_skill<- !is.na(skill_final) && skill_final < 0
  n_anchors <- sum(pred_df$anchored)
  anchor_frac <- n_anchors / nrow(pred_df)
  cat(sprintf(" skill_final=%.3f | anchors=%d/%d | signal_on hours=%d\n",
              ifelse(is.na(skill_final), NaN, skill_final),
              n_anchors, nrow(pred_df), sum(pred_df$signal_on)))

  ## Saved per-market  
  market_outputs[[mkt]]$etfs[[idx]] <- pred_df

  results_log[[length(results_log) + 1]] <- data.frame(
    market = mkt, etf = idx, status = "OK", n_obs = n_obs,
    pseudo_r2 = pseudo_r2, rmse = rmse_val, skill_final = skill_final,
    granger_p = gp, fdr_p = pairs$fdr_p[i], frac_sig = pairs$frac_sig[i],
    lag_used = lag_k, is_overfit = is_overfit, shrink_mult = shrink_mult)
}


## ---- PER-MARKET PASS: one file per market ----
## Brier-skill-weighted consensus across market
cat("\n══════════════════ PER-MARKET PASS ══════════════════\n")
for (mkt in names(market_outputs)) {
  etfs <- market_outputs[[mkt]]$etfs
  cat(sprintf("  Market: %s (%d indices)\n", mkt, length(etfs)))

  combined <- dplyr::bind_rows(lapply(names(etfs), function(e) {
    df <- etfs[[e]]; df$etf_label <- e; df }))

  hours_all <- sort(unique(combined$hour))
  combined_w <- dplyr::bind_rows(lapply(hours_all, function(h) {
    past <- combined %>%
      dplyr::filter(hour < h, !is.na(K_mid_actual), !is.na(fair_value_final))
    w <- sapply(names(etfs), function(e) {
      dp <- past %>% dplyr::filter(etf_label == e)
      if (nrow(dp) < 10) return(0)
      max(1 - mean((dp$fair_value_final - dp$K_mid_actual)^2) /
              mean((0.5 - dp$K_mid_actual)^2), 0)
    })
    if (sum(w) == 0) w[] <- 1
    dd <- combined %>% dplyr::filter(hour == h)
    dd$w <- w[dd$etf_label]
    dd
  }))

  wmean <- function(x, w) {
    w[is.na(x)] <- 0
    if (sum(w) == 0) mean(x, na.rm = TRUE) else sum(x * w, na.rm = TRUE) / sum(w)
  }

  consensus_df <- combined_w %>%
    dplyr::group_by(hour) %>%
    dplyr::summarise(
      market = mkt,
      datetime = datetime[1],
      K_mid_actual = { v <- K_mid_actual[!is.na(K_mid_actual)]
                       if (length(v) > 0) v[1] else NA_real_ },
      fv_consensus = wmean(fair_value_final, w),
      fv_ci_lo = wmean(fv_ci_lo_shrunk, w),
      fv_ci_hi = wmean(fv_ci_hi_shrunk, w),
      signal_on = any(signal_on),
      n_anchored = sum(anchored, na.rm = TRUE),
      n_etfs = dplyr::n(), .groups = "drop") %>%
    dplyr::arrange(hour)

  write.csv(consensus_df %>%
              dplyr::select(market, datetime, fv_consensus, signal_on),
            file.path(OUTPUT_DIR, paste0(mkt, "_fair_value.csv")),
            row.names = FALSE)

  eval_c <- consensus_df %>% dplyr::filter(!is.na(fv_consensus), !is.na(K_mid_actual))
  has_act_c <- nrow(eval_c) >= 2

  pc1 <- ggplot(consensus_df, aes(x = datetime)) +
    { if (any(consensus_df$signal_on))
      geom_rect(data = consensus_df %>% dplyr::filter(signal_on),
                inherit.aes = FALSE,
                aes(xmin = datetime - 1800, xmax = datetime + 1800,
                    ymin = -Inf, ymax = Inf),
                fill = "#1D9E75", alpha = 0.08) } +
    { if (any(!is.na(consensus_df$fv_ci_lo)))
      geom_ribbon(aes(ymin = fv_ci_lo, ymax = fv_ci_hi),
                  fill = "#2a78d6", alpha = 0.12, na.rm = TRUE) } +
    geom_line(aes(y = fv_consensus, colour = "Consensus fair value"),
              linewidth = 0.7, na.rm = TRUE) +
    { if (has_act_c)
      geom_point(data = eval_c, aes(y = K_mid_actual, colour = "Kalshi mid (actual)"),
                 size = 0.9, alpha = 0.6) } +
    scale_colour_manual(values = c("Consensus fair value" = "#2a78d6",
                                   "Kalshi mid (actual)"  = "#e34948"), name = NULL) +
    scale_y_continuous(limits = c(0, 1), labels = scales::percent_format(accuracy = 1)) +
    labs(x = NULL, y = "Probability",
         title = sprintf("%s — hourly dynamic fair value", gsub("_", " ", mkt)),
         subtitle = sprintf(
           "%s of %d screened %s · shaded = signal ON",
           if (length(etfs) > 1) "Brier-skill-weighted average" else "Fair value",
           length(etfs), if (length(etfs) > 1) "indices" else "index")) +
    theme_minimal(base_size = 11) +
    theme(legend.position = "top", panel.grid.minor = element_blank(),
          plot.title = element_text(size = 11, face = "bold"),
          plot.subtitle = element_text(size = 9, colour = "grey50"))

  if (has_act_c) {
    err_c   <- eval_c$fv_consensus - eval_c$K_mid_actual
    skill_c <- 1 - mean(err_c^2) / mean((0.5 - eval_c$K_mid_actual)^2)
    pc2 <- ggplot(eval_c, aes(x = K_mid_actual, y = fv_consensus)) +
      geom_abline(slope = 1, intercept = 0, linetype = "dashed",
                  colour = "grey70", linewidth = 0.6) +
      geom_point(alpha = 0.4, size = 1.2, colour = "#2a78d6") +
      geom_smooth(method = "lm", se = FALSE, colour = "#e34948",
                  linewidth = 0.8, formula = y ~ x) +
      annotate("text", x = 0.05, y = 0.93,
               label = sprintf("RMSE=%.4f  MAE=%.4f\nMBE=%.4f  skill=%.3f",
                               sqrt(mean(err_c^2)), mean(abs(err_c)),
                               mean(err_c), skill_c),
               hjust = 0, size = 3.2, colour = "grey30", family = "mono") +
      scale_x_continuous(limits = c(0, 1), labels = scales::percent_format(accuracy = 1)) +
      scale_y_continuous(limits = c(0, 1), labels = scales::percent_format(accuracy = 1)) +
      labs(x = "Kalshi mid (actual)", y = "Consensus fair value",
           title = "Calibration scatter (consensus)") +
      theme_minimal(base_size = 10) + theme(panel.grid.minor = element_blank())
    pc3 <- reliability_diagram(eval_c$fv_consensus, eval_c$K_mid_actual,
                               title = "Reliability (consensus)")
    pc_out <- if (!is.null(pc3)) pc1 / (pc2 | pc3) + plot_layout(heights = c(2.2, 1.4))
              else pc1 / pc2 + plot_layout(heights = c(2, 1.2))
  } else pc_out <- pc1

  ggsave(file.path(PLOT_DIR, paste0(mkt, ".png")),
         pc_out, width = 10, height = if (has_act_c) 8 else 4.5, dpi = 150, bg = "white")
}

## ---- Summary ----
summary_df <- dplyr::bind_rows(results_log)
write.csv(summary_df, file.path(OUTPUT_DIR, "run_summary.csv"), row.names = FALSE)
