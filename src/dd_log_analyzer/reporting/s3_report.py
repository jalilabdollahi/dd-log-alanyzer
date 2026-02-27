"""S3 report uploader — upload HTML/JSON reports and generate pre-signed URLs."""

from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path

import boto3
from botocore.exceptions import ClientError

logger = logging.getLogger(__name__)


class S3ReportUploader:
    """Upload analysis reports to S3 and generate pre-signed URLs."""

    def __init__(self, bucket_name: str, region: str = "eu-west-2"):
        self._bucket = bucket_name
        self._s3 = boto3.client("s3", region_name=region)

    def upload_report(
        self,
        content: str,
        report_type: str = "html",
        prefix: str = "reports",
    ) -> dict:
        """Upload a report to S3 and return metadata.

        Args:
            content: Report content (HTML or JSON string).
            report_type: Either "html" or "json".
            prefix: S3 key prefix.

        Returns:
            Dict with s3_key, s3_uri, presigned_url.
        """
        timestamp = datetime.utcnow().strftime("%Y-%m-%d/%H-%M-%S")
        content_type = "text/html" if report_type == "html" else "application/json"
        extension = report_type

        s3_key = f"{prefix}/{timestamp}/analysis_report.{extension}"

        try:
            self._s3.put_object(
                Bucket=self._bucket,
                Key=s3_key,
                Body=content.encode("utf-8"),
                ContentType=content_type,
            )

            # Generate pre-signed URL (valid for 7 days)
            presigned_url = self._s3.generate_presigned_url(
                "get_object",
                Params={"Bucket": self._bucket, "Key": s3_key},
                ExpiresIn=7 * 24 * 3600,
            )

            logger.info(f"Report uploaded to s3://{self._bucket}/{s3_key}")

            return {
                "s3_key": s3_key,
                "s3_uri": f"s3://{self._bucket}/{s3_key}",
                "presigned_url": presigned_url,
            }

        except ClientError as e:
            logger.error(f"Failed to upload report to S3: {e}")
            return {"error": str(e)}
