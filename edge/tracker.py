# edge/tracker.py
import numpy as np
from scipy.spatial import distance

def bb_iou(boxA, boxB):
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[0]+boxA[2], boxB[0]+boxB[2])
    yB = min(boxA[1]+boxA[3], boxB[1]+boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = boxA[2] * boxA[3]
    boxBArea = boxB[2] * boxB[3]
    return interArea / float(boxAArea + boxBArea - interArea + 1e-5)

class HybridTracker:
    def __init__(self, max_disappeared=10):
        self.next_object_id = 0
        self.objects = {}
        self.disappeared = {}
        self.max_disappeared = max_disappeared

    def update(self, rects):
        if len(rects) == 0: 
            for obj_id in list(self.disappeared.keys()):
                self.disappeared[obj_id] += 1
                if self.disappeared[obj_id] > self.max_disappeared:
                    del self.objects[obj_id]
                    del self.disappeared[obj_id]
            return self.objects

        input_centroids = np.array([[x+w/2.0, y+h/2.0] for (x, y, w, h) in rects])

        if len(self.objects) == 0:
            for i in range(len(input_centroids)):
                self.objects[self.next_object_id] = (input_centroids[i], rects[i])
                self.disappeared[self.next_object_id] = 0
                self.next_object_id += 1
        else:
            object_ids = list(self.objects.keys())
            object_centroids = [self.objects[oid][0] for oid in object_ids]
            
            # Combine Euclidean Distance and (1-IoU) for robust cost matrix
            D = distance.cdist(np.array(object_centroids), input_centroids)
            
            rows = D.min(axis=1).argsort()
            cols = D.argmin(axis=1)[rows]
            used_rows, used_cols = set(), set()

            for (row, col) in zip(rows, cols):
                if row in used_rows or col in used_cols: continue
                # IoU sanity check to prevent wild jumps
                if bb_iou(self.objects[object_ids[row]][1], rects[col]) < 0.3 or D[row, col] > 80:
                    continue
                
                object_id = object_ids[row]
                self.objects[object_id] = (input_centroids[col], rects[col])
                self.disappeared[object_id] = 0
                used_rows.add(row)
                used_cols.add(col)

            for col in set(range(len(input_centroids))) - used_cols:
                self.objects[self.next_object_id] = (input_centroids[col], rects[col])
                self.disappeared[self.next_object_id] = 0
                self.next_object_id += 1

        return self.objects