/* Attitude control loop. Runs at 1 kHz on the flight computer.
 *
 * Planted fixture for S-2.8. Not compiled and not run — it exists so the
 * real-time screening has something with genuine markers to refuse.
 */

#include "FreeRTOS.h"
#include "task.h"
#include <pthread.h>
#include <sched.h>

/* WCET measured on the target board at 640 us; the budget is 1000 us. */
#define CONTROL_PERIOD_US 1000

static void raise_to_realtime_priority(void)
{
    struct sched_param param;
    param.sched_priority = 80;
    pthread_setschedparam(pthread_self(), SCHED_FIFO, &param);
    mlockall(MCL_CURRENT | MCL_FUTURE);
}

void vControlLoopTask(void *pvParameters)
{
    raise_to_realtime_priority();
    for (;;) {
        read_imu();
        update_attitude();
        drive_actuators();
        vTaskDelay(pdMS_TO_TICKS(1));
    }
}
