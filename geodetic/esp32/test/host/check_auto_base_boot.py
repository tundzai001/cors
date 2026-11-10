"""Every power cycle requests fresh AutoNearest, including after saved Fixed LLA."""
from pathlib import Path
import subprocess
import tempfile

root = Path(__file__).resolve().parents[2]
source = (root / 'src/functions/backend/Backend_Agent_Startup.cpp').read_text()
startup = source[source.index('bool BackendAgent::initializeBaseAtBoot('):source.index('bool BackendAgent::readStartupReceiverMode(')]
shim = (root / 'test/host/Arduino.h').read_text().replace('#pragma once', '')
harness = r'''
#include <cassert>
#include <ArduinoJson.h>
namespace GnssCmdBuilder { enum class GnssSensorType { Invalid, Ublox, Unicore }; }
constexpr size_t CONFIG_DOCUMENT_SIZE = 4096;
struct BackendAgent {
    bool baseStartupAttempted_ = false, saved = true, dirty = false;
    struct { bool all = false; } stationLock_;
    String baseConfig_ = "{}", sensorType_, baseStartupState_, baseStartupError_;
    unsigned saves = 0;
    bool saveStatePreferences() { ++saves; return saved; }
    void markStatusDirty() { dirty = true; }
    bool initializeBaseAtBoot(GnssCmdBuilder::GnssSensorType);
};
'''
checks = r'''
int main() {
    using GnssCmdBuilder::GnssSensorType;
    for (auto chip : {GnssSensorType::Ublox, GnssSensorType::Unicore}) {
        for (const char *method : {"FIXED_LLA", "SURVEY_IN", "AUTO_CORS", ""}) {
            BackendAgent agent;
            DynamicJsonDocument config(4096);
            config["base_setup_method"] = method;
            config["auto_cors_source_mode"] = "manual";
            config["coords"]["lat"] = 21;
            config["gnss_options"]["msm_level"] = "7";
            agent.baseConfig_.clear();
            serializeJson(config, agent.baseConfig_);
            assert(agent.initializeBaseAtBoot(chip));
            assert(agent.baseStartupState_ == "awaiting_auto_base");
            deserializeJson(config, agent.baseConfig_);
            assert(config["base_setup_method"] == "AUTO_CORS");
            assert(config["auto_cors_source_mode"] == "nearest");
            assert(!config.containsKey("coords"));
            assert(config["gnss_options"]["msm_level"] == "7");
            assert(agent.initializeBaseAtBoot(chip) && agent.saves == 1);
        }
    }
    BackendAgent fresh;
    assert(fresh.initializeBaseAtBoot(GnssSensorType::Ublox));
    DynamicJsonDocument defaults(4096); deserializeJson(defaults, fresh.baseConfig_);
    assert(defaults["gnss_options"]["output_mode"] == "rtcm_only");
    BackendAgent locked; locked.stationLock_.all = true;
    assert(locked.initializeBaseAtBoot(GnssSensorType::Ublox));
    assert(locked.baseStartupState_ == "locked" && locked.saves == 0);
    BackendAgent failed; failed.saved = false;
    assert(!failed.initializeBaseAtBoot(GnssSensorType::Ublox));
    assert(failed.baseStartupState_ == "error");
    BackendAgent unknown;
    assert(!unknown.initializeBaseAtBoot(GnssSensorType::Invalid));
    assert(!unknown.baseStartupAttempted_ && unknown.saves == 0);
    assert(unknown.initializeBaseAtBoot(GnssSensorType::Ublox));
}
'''
with tempfile.TemporaryDirectory(prefix='auto-base-boot-') as directory:
    path = Path(directory)
    (path / 'check.cpp').write_text(shim + harness + startup + checks)
    subprocess.run(['c++', '-std=c++17', '-Wall', '-Wextra', '-Werror', '-fsanitize=address,undefined',
                    '-I' + str(root / '.pio/libdeps/tdm240x_dev_general/ArduinoJson/src'),
                    str(path / 'check.cpp'), '-o', str(path / 'check')], check=True)
    subprocess.run([str(path / 'check')], check=True)
print('PASS: fresh AutoNearest at boot for both chips and saved modes; options, lock, persistence failure and detection retry')
