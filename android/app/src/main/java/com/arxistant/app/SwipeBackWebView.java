package com.arxistant.app;

import android.content.Context;
import android.view.MotionEvent;
import android.webkit.WebView;

/**
 * WebView that treats a horizontal swipe starting at either screen edge as a
 * "back" gesture: swiping right from the left edge, or swiping left from the
 * right edge (左滑返回). On phones with gesture navigation the system already
 * maps edge swipes to the back action (handled in MainActivity); this makes
 * the same gesture work on 3-button-navigation phones, where edge swipes
 * would otherwise do nothing.
 */
public class SwipeBackWebView extends WebView {
    public interface OnSwipeBackListener {
        void onSwipeBack();
    }

    private final OnSwipeBackListener listener;
    private final int edgeWidthPx;
    private final int minSwipePx;
    private float downX;
    private float downY;
    private boolean tracking;

    public SwipeBackWebView(Context context, OnSwipeBackListener listener) {
        super(context);
        this.listener = listener;
        float density = context.getResources().getDisplayMetrics().density;
        edgeWidthPx = (int) (48 * density);
        minSwipePx = (int) (90 * density);
    }

    @Override
    public boolean dispatchTouchEvent(MotionEvent event) {
        switch (event.getActionMasked()) {
            case MotionEvent.ACTION_DOWN:
                downX = event.getX();
                downY = event.getY();
                tracking = downX < edgeWidthPx
                        || downX > getWidth() - edgeWidthPx;
                break;
            case MotionEvent.ACTION_UP:
                if (tracking && listener != null) {
                    float dx = event.getX() - downX;
                    float dy = event.getY() - downY;
                    boolean rightSwipeFromLeftEdge =
                            downX < edgeWidthPx && dx > minSwipePx;
                    boolean leftSwipeFromRightEdge =
                            downX > getWidth() - edgeWidthPx && dx < -minSwipePx;
                    if ((rightSwipeFromLeftEdge || leftSwipeFromRightEdge)
                            && Math.abs(dx) > 2 * Math.abs(dy)) {
                        tracking = false;
                        listener.onSwipeBack();
                        return true;
                    }
                }
                tracking = false;
                break;
            case MotionEvent.ACTION_CANCEL:
                // The system (e.g. gesture navigation) took over the gesture;
                // it will deliver the back action itself.
                tracking = false;
                break;
            default:
                break;
        }
        return super.dispatchTouchEvent(event);
    }
}
