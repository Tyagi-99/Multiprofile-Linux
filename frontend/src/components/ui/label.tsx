import { forwardRef, type LabelHTMLAttributes } from "react";
import { cn } from "../../lib/cn";

/** Label: form label. */

export const Label = forwardRef<HTMLLabelElement, LabelHTMLAttributes<HTMLLabelElement>>(
  ({ className, ...rest }, ref) => (
    <label
      ref={ref}
      className={cn("text-[13px] font-medium text-ink-2", className)}
      {...rest}
    />
  ),
);
Label.displayName = "Label";
