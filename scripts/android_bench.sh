#!/usr/bin/env bash
# scripts/android_bench.sh
# Task 0.3: On-device micro-benchmarks, sustained thermal run, and baseline.
#
# Usage:
#   ./scripts/android_bench.sh push                 # Push benchmark_model and all candidate models
#   ./scripts/android_bench.sh microbench           # Run full micro-benchmark matrix
#   ./scripts/android_bench.sh sustained [minutes]  # Run 10-min sustained load & log thermals
#   ./scripts/android_bench.sh baseline [minutes]   # Run 20-min idle baseline
#   ./scripts/android_bench.sh all                  # Run push + microbench

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="${ROOT_DIR}/out"
MODELS_DIR="${OUT_DIR}/android_models"
DEVICE_TMP="/data/local/tmp"
DEVICE_MODELS="${DEVICE_TMP}/models"
BENCH_BIN="${DEVICE_TMP}/benchmark_model"
RESULTS_CSV="${OUT_DIR}/android_bench_results.csv"

# Color output helpers
RED='\033[0;31m'
GREEN='\033[0;32m'
BLUE='\033[0;34m'
YELLOW='\033[1;33m'
NC='\033[0m'

log_info() { echo -e "${BLUE}[INFO]${NC} $*"; }
log_ok() { echo -e "${GREEN}[OK]${NC} $*"; }
log_warn() { echo -e "${YELLOW}[WARN]${NC} $*"; }
log_err() { echo -e "${RED}[ERROR]${NC} $*"; }

check_adb() {
    if ! command -v adb >/dev/null 2>&1; then
        log_err "adb not found in PATH"
        exit 1
    fi
    local devices
    devices=$(adb devices | grep -v "List" | grep "device" || true)
    if [ -z "${devices}" ]; then
        log_err "No Android device connected via adb with 'device' state"
        adb devices
        exit 1
    fi
    local dev_model dev_board dev_abi dev_ver
    dev_model=$(adb shell getprop ro.product.model | tr -d '\r')
    dev_board=$(adb shell getprop ro.board.platform | tr -d '\r')
    dev_abi=$(adb shell getprop ro.product.cpu.abi | tr -d '\r')
    dev_ver=$(adb shell getprop ro.build.version.release | tr -d '\r')
    log_info "Target Device: ${dev_model} (${dev_board}), ABI: ${dev_abi}, Android: ${dev_ver}"
}

# Determine big core affinity mask
get_big_core_mask() {
    # On Helio G99 (mt6789): 8 cores, cpu0-5 @ 2.0GHz, cpu6-7 @ 2.2GHz
    # Mask for cores 6,7 is 0xc0
    local freqs
    freqs=$(adb shell "cat /sys/devices/system/cpu/cpu*/cpufreq/cpuinfo_max_freq 2>/dev/null || true" | tr -d '\r')
    if [ -n "${freqs}" ]; then
        local max_freq=0
        for f in ${freqs}; do
            if [ "$f" -gt "$max_freq" ]; then
                max_freq="$f"
            fi
        done
        local mask=0
        local idx=0
        for f in ${freqs}; do
            if [ "$f" -eq "$max_freq" ]; then
                mask=$((mask | (1 << idx)))
            fi
            idx=$((idx + 1))
        done
        printf "%x\n" "$mask"
    else
        echo "c0"
    fi
}

cmd_push() {
    check_adb
    mkdir -p "${OUT_DIR}"
    
    # 1. Ensure benchmark_model binary is present on host
    local host_bin="/tmp/benchmark_model"
    if [ ! -f "${host_bin}" ]; then
        log_info "Downloading prebuilt LiteRT benchmark_model for Android arm64..."
        curl -sSL "https://storage.googleapis.com/tensorflow-nightly-public/prod/tensorflow/release/lite/tools/nightly/latest/android_aarch64_benchmark_model" -o "${host_bin}"
        chmod +x "${host_bin}"
    fi

    # 2. Push benchmark_model binary to device
    log_info "Pushing benchmark_model to ${BENCH_BIN}..."
    adb push "${host_bin}" "${BENCH_BIN}" >/dev/null
    adb shell chmod +x "${BENCH_BIN}"

    # 3. Push all candidate models
    log_info "Creating device directory ${DEVICE_MODELS}..."
    adb shell mkdir -p "${DEVICE_MODELS}"

    local models=(
        "det_yolo26n_320_int8.tflite"
        "det_yolo26n_320_fp32.tflite"
        "det_effdet_lite0_320_int8.tflite"
        "det_picodet_s_320_tflite.tflite"
        "pose_rtmpose_ap10k_litert.tflite"
        "pose_superanimal_hrnet_w32.tflite"
        "face_dog_landmarks_384.tflite"
        "audio_yamnet.tflite"
    )

    for m in "${models[@]}"; do
        local p="${MODELS_DIR}/${m}"
        if [ -f "${p}" ]; then
            log_info "Pushing model: ${m} ($(ls -lh "${p}" | awk '{print $5}'))..."
            adb push "${p}" "${DEVICE_MODELS}/${m}" >/dev/null
        else
            log_warn "Candidate model not found on host: ${p}"
        fi
    done
    log_ok "All models and benchmark tool pushed successfully!"
}

parse_and_record() {
    local model_name="$1"
    local backend="$2"
    local threads="$3"
    local pinned="$4"
    local raw_output="$5"

    local init_ms=""
    local warmup_ms=""
    local avg_ms=""
    local median_ms=""
    local p95_ms=""
    local mem_delta_mb=""

    # Parse Init, Warmup, Inference avg
    # INFO: Inference timings in us: Init: 45432, First inference: 33194, Warmup (avg): 19652.2, Inference (avg): 18970.3
    if echo "${raw_output}" | grep -q "Inference timings in us:"; then
        local timings_line
        timings_line=$(echo "${raw_output}" | grep "Inference timings in us:" | head -n 1)
        local init_us
        init_us=$(echo "${timings_line}" | sed -n 's/.*Init: \([0-9.]*\).*/\1/p')
        local warmup_us
        warmup_us=$(echo "${timings_line}" | sed -n 's/.*Warmup (avg): \([0-9.]*\).*/\1/p')
        local avg_us
        avg_us=$(echo "${timings_line}" | sed -n 's/.*Inference (avg): \([0-9.]*\).*/\1/p')

    if [ -n "${init_us}" ]; then init_ms=$(awk "BEGIN {printf \"%.2f\", ${init_us}/1000}"); fi
    if [ -n "${warmup_us}" ]; then warmup_ms=$(awk "BEGIN {printf \"%.2f\", ${warmup_us}/1000}"); fi
    if [ -n "${avg_us}" ]; then avg_ms=$(awk "BEGIN {printf \"%.2f\", ${avg_us}/1000}"); fi
    fi

    # Parse median and p95 from steady state line
    # INFO: count=51 first=19166 curr=18820 min=16964 max=19334 avg=18970.3 std=417 p5=18561 median=19049 p95=19274
    local steady_line
    steady_line=$(echo "${raw_output}" | grep "count=" | tail -n 1 || true)
    if [ -n "${steady_line}" ]; then
        local med_us
        med_us=$(echo "${steady_line}" | sed -n 's/.*median=\([0-9.]*\).*/\1/p')
        local p95_us
        p95_us=$(echo "${steady_line}" | sed -n 's/.*p95=\([0-9.]*\).*/\1/p')
        if [ -n "${med_us}" ]; then median_ms=$(awk "BEGIN {printf \"%.2f\", ${med_us}/1000}"); fi
        if [ -n "${p95_us}" ]; then p95_ms=$(awk "BEGIN {printf \"%.2f\", ${p95_us}/1000}"); fi
    fi

    # Parse memory footprint
    # INFO: Memory footprint delta from the start of the tool (MB): init=18.0469 overall=21.5547
    if echo "${raw_output}" | grep -q "Memory footprint delta"; then
        mem_delta_mb=$(echo "${raw_output}" | grep "Memory footprint delta" | sed -n 's/.*overall=\([0-9.]*\).*/\1/p')
        if [ -n "${mem_delta_mb}" ]; then mem_delta_mb=$(awk "BEGIN {printf \"%.1f\", ${mem_delta_mb}}"); fi
    fi

    echo "${model_name},${backend},${threads},${pinned},${init_ms},${warmup_ms},${avg_ms},${median_ms},${p95_ms},${mem_delta_mb}" >> "${RESULTS_CSV}"
    printf "| %-32s | %-12s | %2s | %-6s | %8s | %8s | %8s | %8s | %8s | %7s |\n" \
        "${model_name}" "${backend}" "${threads}" "${pinned}" "${init_ms}" "${warmup_ms}" "${avg_ms}" "${median_ms}" "${p95_ms}" "${mem_delta_mb}"
}

cmd_microbench() {
    check_adb
    local big_mask
    big_mask=$(get_big_core_mask)
    log_info "Big core affinity mask: 0x${big_mask}"

    echo "model,backend,threads,pinned,init_ms,warmup_ms,mean_ms,median_ms,p95_ms,pss_delta_mb" > "${RESULTS_CSV}"

    log_info "Running micro-benchmarks on device..."
    echo "---------------------------------------------------------------------------------------------------------------------------------"
    printf "| %-32s | %-12s | %2s | %-6s | %8s | %8s | %8s | %8s | %8s | %7s |\n" \
        "Model" "Backend" "Th" "Pinned" "Init(ms)" "Warm(ms)" "Mean(ms)" "Med(ms)" "p95(ms)" "PSS(MB)"
    echo "---------------------------------------------------------------------------------------------------------------------------------"

    local models=(
        "det_yolo26n_320_int8.tflite"
        "det_yolo26n_320_fp32.tflite"
        "det_effdet_lite0_320_int8.tflite"
        "det_picodet_s_320_tflite.tflite"
        "pose_rtmpose_ap10k_litert.tflite"
        "pose_superanimal_hrnet_w32.tflite"
        "face_dog_landmarks_384.tflite"
        "audio_yamnet.tflite"
    )

    for m in "${models[@]}"; do
        local dev_model_path="${DEVICE_MODELS}/${m}"
        # Check if file exists on device
        if ! adb shell "[ -f '${dev_model_path}' ]"; then
            log_warn "Model missing on device: ${dev_model_path}, pushing..."
            adb push "${MODELS_DIR}/${m}" "${dev_model_path}" >/dev/null
        fi

        # 1. CPU XNNPACK - Big cores pinned (threads = 1, 2, 4)
        for th in 1 2 4; do
            local out
            out=$(adb shell "taskset ${big_mask} ${BENCH_BIN} --graph=${dev_model_path} --num_threads=${th} --use_xnnpack=true --num_runs=20 2>&1" || true)
            if echo "${out}" | grep -q "Inference timings in us:"; then
                parse_and_record "${m}" "CPU_XNNPACK" "${th}" "big_cores" "${out}"
            elif [ "${m}" = "pose_rtmpose_ap10k_litert.tflite" ]; then
                # Fallback to standard CPU reference without XNNPACK (depthwise conv op issue)
                local out_noxnn
                out_noxnn=$(adb shell "taskset ${big_mask} ${BENCH_BIN} --graph=${dev_model_path} --num_threads=${th} --use_xnnpack=false --num_runs=20 2>&1" || true)
                parse_and_record "${m}" "CPU_NOXNN" "${th}" "big_cores" "${out_noxnn}"
            else
                parse_and_record "${m}" "CPU_XNNPACK" "${th}" "big_cores" "${out}"
            fi
        done

        # 2. CPU XNNPACK - Unpinned default (threads = 2)
        local out_unpinned
        out_unpinned=$(adb shell "${BENCH_BIN} --graph=${dev_model_path} --num_threads=2 --use_xnnpack=true --num_runs=20 2>&1" || true)
        if echo "${out_unpinned}" | grep -q "Inference timings in us:"; then
            parse_and_record "${m}" "CPU_XNNPACK" "2" "default" "${out_unpinned}"
        elif [ "${m}" = "pose_rtmpose_ap10k_litert.tflite" ]; then
            local out_unpinned_noxnn
            out_unpinned_noxnn=$(adb shell "${BENCH_BIN} --graph=${dev_model_path} --num_threads=2 --use_xnnpack=false --num_runs=20 2>&1" || true)
            parse_and_record "${m}" "CPU_NOXNN" "2" "default" "${out_unpinned_noxnn}"
        else
            parse_and_record "${m}" "CPU_XNNPACK" "2" "default" "${out_unpinned}"
        fi

        # 3. GPU Delegate (OpenCL)
        local out_gpu
        out_gpu=$(adb shell "${BENCH_BIN} --graph=${dev_model_path} --use_gpu=true --num_runs=20 2>&1" || true)
        if echo "${out_gpu}" | grep -q "Inference timings in us:"; then
            parse_and_record "${m}" "GPU_OPENCL" "1" "gpu" "${out_gpu}"
        else
            log_warn "${m}: GPU delegate failed or not supported by ops"
        fi
    done

    echo "---------------------------------------------------------------------------------------------------------------------------------"
    log_ok "Micro-benchmarks complete! Results written to ${RESULTS_CSV}"
}

cmd_sustained() {
    check_adb
    local duration_min="${1:-10}"
    local duration_s=$((duration_min * 60))
    local big_mask
    big_mask=$(get_big_core_mask)

    local det_model="${DEVICE_MODELS}/det_yolo26n_320_int8.tflite"
    local pose_model="${DEVICE_MODELS}/pose_rtmpose_ap10k_litert.tflite"

    log_info "Starting sustained load test: ${duration_min} minutes (${duration_s} s)..."
    log_info "Simulating nominal pipeline: Detector @ 3 Hz + Pose @ 6 Hz"

    local log_file="${OUT_DIR}/sustained_thermal.log"
    echo "timestamp_s,thermal_status,cpu_temp_c,gpu_temp_c,soc_temp_c,battery_temp_c,cpu6_freq_khz,cpu0_freq_khz" > "${log_file}"

    local start_time
    start_time=$(date +%s)
    local end_time=$((start_time + duration_s))

    # Launch background loop on device simulating detector + pose workload
    # We run benchmark_model in a continuous loop for the duration
    adb shell "sh -c '
        END=\$(( \$(date +%s) + ${duration_s} ))
        while [ \$(date +%s) -lt \$END ]; do
            taskset ${big_mask} ${BENCH_BIN} --graph=${det_model} --num_threads=2 --use_xnnpack=true --num_runs=15 >/dev/null 2>&1
            ${BENCH_BIN} --graph=${pose_model} --use_gpu=true --num_runs=30 >/dev/null 2>&1
        done
    '" &
    local workload_pid=$!

    log_info "Polling device thermals and frequencies every 10 s..."
    while [ "$(date +%s)" -lt "${end_time}" ]; do
        local elapsed=$(( $(date +%s) - start_time ))
        local th_status
        th_status=$(adb shell dumpsys thermalservice | grep "Thermal Status:" | awk '{print $3}' | tr -d '\r')
        local temps
        temps=$(adb shell dumpsys thermalservice | grep -E "Temperature{" | tr -d '\r' || true)
        local cpu_temp
        cpu_temp=$(echo "${temps}" | grep "mName=CPU" | head -n 1 | sed -n 's/.*mValue=\([0-9.]*\).*/\1/p')
        cpu_temp=${cpu_temp:-0}
        local gpu_temp
        gpu_temp=$(echo "${temps}" | grep "mName=GPU" | head -n 1 | sed -n 's/.*mValue=\([0-9.]*\).*/\1/p')
        gpu_temp=${gpu_temp:-0}
        local soc_temp
        soc_temp=$(echo "${temps}" | grep "mName=SOC" | head -n 1 | sed -n 's/.*mValue=\([0-9.]*\).*/\1/p')
        soc_temp=${soc_temp:-0}
        local batt_temp_raw
        batt_temp_raw=$(adb shell dumpsys battery | grep "^\s*temperature:" | head -n 1 | awk '{print $2}' | tr -d '\r')
        local batt_temp
        batt_temp=$(awk "BEGIN {printf \"%.1f\", ${batt_temp_raw}/10}")
        local cpu6_freq
        cpu6_freq=$(adb shell cat /sys/devices/system/cpu/cpu6/cpufreq/scaling_cur_freq 2>/dev/null | tr -d '\r' || echo "0")
        local cpu0_freq
        cpu0_freq=$(adb shell cat /sys/devices/system/cpu/cpu0/cpufreq/scaling_cur_freq 2>/dev/null | tr -d '\r' || echo "0")

        echo "${elapsed},${th_status},${cpu_temp},${gpu_temp},${soc_temp},${batt_temp},${cpu6_freq},${cpu0_freq}" >> "${log_file}"
        printf "t=%4ds | ThermalStatus=%s | CPU=%5.1f°C | GPU=%5.1f°C | Batt=%5.1f°C | BigCore=%s kHz\n" \
            "${elapsed}" "${th_status}" "${cpu_temp}" "${gpu_temp}" "${batt_temp}" "${cpu6_freq}"
        sleep 10
    done

    wait "${workload_pid}" 2>/dev/null || true
    log_ok "Sustained test finished! Log written to ${log_file}"
}

cmd_baseline() {
    check_adb
    local duration_min="${1:-20}"
    local duration_s=$((duration_min * 60))

    log_info "Measuring zero-inference baseline power and thermal floor for ${duration_min} min..."
    local log_file="${OUT_DIR}/baseline_power.log"
    echo "timestamp_s,battery_level_pct,battery_temp_c,thermal_status" > "${log_file}"

    local start_time
    start_time=$(date +%s)
    local end_time=$((start_time + duration_s))

    while [ "$(date +%s)" -lt "${end_time}" ]; do
        local elapsed=$(( $(date +%s) - start_time ))
        local batt_level
        batt_level=$(adb shell dumpsys battery | grep "^\s*level:" | head -n 1 | awk '{print $2}' | tr -d '\r')
        local batt_temp_raw
        batt_temp_raw=$(adb shell dumpsys battery | grep "^\s*temperature:" | head -n 1 | awk '{print $2}' | tr -d '\r')
        local batt_temp
        batt_temp=$(awk "BEGIN {printf \"%.1f\", ${batt_temp_raw}/10}")
        local th_status
        th_status=$(adb shell dumpsys thermalservice | grep "Thermal Status:" | awk '{print $3}' | tr -d '\r')

        echo "${elapsed},${batt_level},${batt_temp},${th_status}" >> "${log_file}"
        printf "t=%4ds | Battery=%s%% | Temp=%5.1f°C | Status=%s\n" \
            "${elapsed}" "${batt_level}" "${batt_temp}" "${th_status}"
        sleep 30
    done

    log_ok "Baseline measurement complete! Log written to ${log_file}"
}

cmd_soak() {
    check_adb
    local duration_min="${1:-60}"
    local duration_s=$((duration_min * 60))

    log_info "Starting soak test for ${duration_min} minutes on com.btb.ondevice..."
    local log_file="${OUT_DIR}/soak_run.csv"
    echo "elapsed_s,pid,pss_kb,battery_pct,batt_temp_c,thermal_status" > "${log_file}"

    local start_time
    start_time=$(date +%s)
    local end_time=$((start_time + duration_s))
    local initial_batt
    initial_batt=$(adb shell dumpsys battery | grep "^\s*level:" | head -n 1 | awk '{print $2}' | tr -d '\r')

    while [ "$(date +%s)" -lt "${end_time}" ]; do
        local elapsed=$(( $(date +%s) - start_time ))
        local pid
        pid=$(adb shell pidof com.btb.ondevice 2>/dev/null | tr -d '\r' || echo "")
        
        if [ -z "${pid}" ]; then
            log_err "Process com.btb.ondevice is NOT running! Restarting or exiting..."
        fi

        local pss_kb
        pss_kb=$(adb shell dumpsys meminfo com.btb.ondevice 2>/dev/null | grep "TOTAL PSS:" | awk '{print $3}' | tr -d '\r' || echo "0")
        pss_kb=$(echo "${pss_kb}" | grep -E "^[0-9]+$" || echo "0")
        local pss_mb
        pss_mb=$(awk "BEGIN {printf \"%.1f\", ${pss_kb:-0}/1024}")

        local batt_level
        batt_level=$(adb shell dumpsys battery | grep "^\s*level:" | head -n 1 | awk '{print $2}' | tr -d '\r')
        local batt_temp_raw
        batt_temp_raw=$(adb shell dumpsys battery | grep "^\s*temperature:" | head -n 1 | awk '{print $2}' | tr -d '\r')
        local batt_temp
        batt_temp=$(awk "BEGIN {printf \"%.1f\", ${batt_temp_raw:-0}/10}")
        local th_status
        th_status=$(adb shell dumpsys thermalservice 2>/dev/null | grep "Thermal Status:" | awk '{print $3}' | tr -d '\r' || echo "0")

        echo "${elapsed},${pid:-0},${pss_kb:-0},${batt_level:-0},${batt_temp},${th_status}" >> "${log_file}"
        printf "t=%4ds | PID=%-6s | PSS=%6.1f MB | Battery=%3s%% | Temp=%5.1f°C | ThermalStatus=%s\n" \
            "${elapsed}" "${pid:-DEAD}" "${pss_mb}" "${batt_level}" "${batt_temp}" "${th_status}"
        sleep 10
    done

    log_ok "Soak measurement complete! Results written to ${log_file}"
}

case "${1:-help}" in
    push) cmd_push ;;
    microbench) cmd_microbench ;;
    sustained) cmd_sustained "${2:-10}" ;;
    baseline) cmd_baseline "${2:-20}" ;;
    soak) cmd_soak "${2:-60}" ;;
    all)
        cmd_push
        cmd_microbench
        ;;
    *)
        echo "Usage: $0 {push|microbench|sustained [min]|baseline [min]|soak [min]|all}"
        exit 1
        ;;
esac
