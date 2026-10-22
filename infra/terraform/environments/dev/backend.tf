# Remote state backend. These values are placeholders: the S3 bucket and DynamoDB lock
# table they name must be created out-of-band, by hand or in a separate bootstrap
# configuration, BEFORE this backend block can be initialized for real — Terraform
# cannot create the backend that stores its own state as part of the same `apply`.
#
# Bootstrap once (outside this configuration, e.g. via the AWS CLI or console):
#   aws s3api create-bucket --bucket <name>-tfstate --region <region> \
#     --create-bucket-configuration LocationConstraint=<region>
#   aws s3api put-bucket-versioning --bucket <name>-tfstate \
#     --versioning-configuration Status=Enabled
#   aws dynamodb create-table --table-name <name>-tfstate-locks \
#     --attribute-definitions AttributeName=LockID,AttributeType=S \
#     --key-schema AttributeName=LockID,KeyType=HASH \
#     --billing-mode PAY_PER_REQUEST
#
# Until that exists, validate with `terraform init -backend=false` (see
# ./README.md) rather than editing these placeholders.
terraform {
  backend "s3" {
    bucket         = "alphadots-dev-tfstate"
    key            = "alphadots/dev/terraform.tfstate"
    region         = "us-east-1"
    dynamodb_table = "alphadots-dev-tfstate-locks"
    encrypt        = true
  }
}
