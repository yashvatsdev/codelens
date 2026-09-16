"use client";

import React, { useMemo } from "react";

export interface DonutChartSegment {
  label: string;
  value: number;
  color: string; 
}

interface DonutChartProps {
  segments: DonutChartSegment[];
  totalLabel: string;
  totalValue: number;
  size?: number;
  strokeWidth?: number;
  className?: string;
}

export function DonutChart({
  segments,
  totalLabel,
  totalValue,
  size = 160,
  strokeWidth = 24,
  className = "",
}: DonutChartProps) {
  const center = size / 2;
  const radius = center - strokeWidth / 2;
  const circumference = 2 * Math.PI * radius;

  // Calculate offsets for each segment
  const renderedSegments = useMemo(() => {
    let currentOffset = 0;

    const result = segments.map((segment) => {
      const percentage = totalValue > 0 ? segment.value / totalValue : 0;
      const strokeLength = percentage * circumference;
      
      const segmentData = {
        ...segment,
        strokeDasharray: `${strokeLength} ${circumference}`,
        strokeDashoffset: -currentOffset,
      };

      currentOffset += strokeLength;
      return segmentData;
    });

    return result;
  }, [segments, totalValue, circumference]);

  return (
    <div className={`relative flex items-center justify-center ${className}`} style={{ width: size, height: size }}>
      <svg
        width={size}
        height={size}
        viewBox={`0 0 ${size} ${size}`}
        className="-rotate-90 transform"
      >
        {totalValue === 0 && (
          <circle
            cx={center}
            cy={center}
            r={radius}
            fill="transparent"
            stroke="currentColor"
            strokeWidth={strokeWidth}
            className="text-white/10"
          />
        )}
        
        {totalValue > 0 && renderedSegments.map((segment, idx) => (
          <circle
            key={`${segment.label}-${idx}`}
            cx={center}
            cy={center}
            r={radius}
            fill="transparent"
            stroke={segment.color}
            strokeWidth={strokeWidth}
            strokeDasharray={segment.strokeDasharray}
            strokeDashoffset={segment.strokeDashoffset}
            strokeLinecap="butt"
            className="transition-all duration-500 ease-in-out"
          />
        ))}
      </svg>
      
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <span className="text-2xl font-bold font-mono text-zinc-100">
          {totalValue}
        </span>
        <span className="text-[10px] uppercase tracking-wider text-zinc-500 font-medium">
          {totalLabel}
        </span>
      </div>
    </div>
  );
}
