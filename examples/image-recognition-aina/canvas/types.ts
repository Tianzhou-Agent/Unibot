// The results of this example's MCP tools (../server/main.py).
export interface VisionHealth {
  status: string;
  model: string;
  device: string;
  requested_device: string;
  gpu_name?: string | null;
}

export interface VisionBoundingBox {
  x1: number;
  y1: number;
  x2: number;
  y2: number;
}

export interface VisionDetection {
  class_id: number;
  label: string;
  label_zh: string;
  confidence: number;
  box: VisionBoundingBox;
}

export interface VisionDetectionResponse {
  model: string;
  device: string;
  image: {
    width: number;
    height: number;
  };
  detections: VisionDetection[];
  summary: Record<string, number>;
  inference_ms: number;
}
