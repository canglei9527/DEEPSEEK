package com.powerlens.app;

import android.app.job.JobParameters;
import android.app.job.JobService;
import android.os.Handler;
import android.os.Looper;

public class CollectJobService extends JobService {

    @Override
    public boolean onStopJob(JobParameters jobParameters) {
        return false;
    }

    @Override
    public boolean onStartJob(final JobParameters jobParameters) {
        new Thread(new Runnable() {
            @Override
            public void run() {
                try {
                    Collector.collectOnce(CollectJobService.this);
                } catch (Throwable ignored) {
                }
                new Handler(Looper.getMainLooper()).post(new Runnable() {
                    @Override
                    public void run() {
                        try {
                            jobFinished(jobParameters, false);
                        } catch (Throwable ignored) {
                        }
                    }
                });
            }
        }, "pl-collect").start();
        return true;
    }
}