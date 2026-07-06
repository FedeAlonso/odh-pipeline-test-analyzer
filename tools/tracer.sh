#!/bin/bash
export > /dev/null 2>&1


BUILD_TYPE=ci
IMAGE_TYPE=fbc
TAG=
INPUT_VERSION=
DIGEST=
SHOW_COMMITS=
SEARCH_PARAM=
IMAGE=
CONFIGURE=
UPDATE=
ODH_MODE=false
GA_MODE=false
IMAGE_URI=
FULL_IMAGE_URI_WITH_DIGEST=
TEXT_OUTPUT=
OUTPUT_FORMAT=text

# these are defined once odh mode is determined 
BUILD_CONFIG_REPO=
QUAY_BASE_URL=
FBC_QUAY_REPO=
BUNDLE_QUAY_REPO=

function help() {
  echo "Usage: tracer.sh [-h] [-v] [-c] [-s] [-n] [-b] [-o] [-g] [-f FORMAT] [configure] [update]"
  echo "  -h, --help - Display this help message"
  echo "  -v, --version, --rhoai-version - RHOAI version to get the build info for, valid formats are X.Y, vX.Y, or rhoai-X.Y (case-insensitive). Not compatible with the --odh flag"
  echo "  -d, --digest - Complete digest of the image to be provided as an input, optional, if rhoai-verson and digest both are provided then digest will take precedence"
  echo "  -c --show-commits - Show the commits info for all the components, by default only basic info is shown"
  echo "  -s --search - search to see if a particular code commit is in the build.  Use the format REPO_NAME/SHA_PREFIX. REPO_NAME can be a partial match"
  echo "  -n --nightly - Show the info of latest nightly build, by default the CI-build info is shown"
  echo "  -g, --ga, --stable - Show the info of the latest GA (prod-released) build, resolved from vX.Y.Z-ga tags in quay. Supports both X.Y (latest GA) and X.Y.Z (exact GA). Not compatible with --nightly or --odh"
  echo "  -b --bundle - Show the info about operator bundle image, by default it will show the FBC image info"
  echo "  -o, --odh - Use ODH mode with opendatahub repositories. Uses the odh-stable tag. Not compatible with the --version flag"
  echo "  -f, --output - Output format: text (default), json, or yaml. Requires -c flag for component data"
  echo "  -i --image - Complete URI of the image to be provided as an input, optional, if image and digest both are provided then image will take precedence, it suppports all the image formats - :tag, @sha256:digest and :tag@sha256:digest"
  echo " configure - To configure the tracer and skopeo as needed"
  echo " update - To update the tracer to latest version available in the repo"
}


POSITIONAL=()
while [[ $# -gt 0 ]]; do
    key="$1"
    case $key in
        --help | -h)
        help
        exit
        ;;
        --version | -v)
        INPUT_VERSION="$2"
        shift
        shift
        ;;
        --rhoai-version)
        INPUT_VERSION="$2"
        shift
        shift
        ;;
        --digest | -d)
        DIGEST="$2"
        shift
        shift
        ;;
        --nightly | -n)
        BUILD_TYPE=nightly
        shift
        ;;
        --ga | --stable | -g)
        GA_MODE=true
        shift
        ;;
        --show-commits | -c)
        SHOW_COMMITS=true
        shift
        ;;
        --search | -s)
        SEARCH_PARAM=$2
        shift
        shift
        ;;
        --bundle | -b)
        IMAGE_TYPE=bundle
        shift
        ;;
        --odh | -o)
        ODH_MODE=true
        shift
        ;;
        --output | -f)
        OUTPUT_FORMAT="$2"
        if [[ "$OUTPUT_FORMAT" != "json" && "$OUTPUT_FORMAT" != "yaml" && "$OUTPUT_FORMAT" != "text" ]]; then
          echo "Error: --output must be one of: text, json, yaml (got: '$OUTPUT_FORMAT')"
          exit 1
        fi
        shift
        shift
        ;;
        --image | -i)
        IMAGE="$2"
        shift
        shift
        ;;
        configure)
        CONFIGURE=true
        shift
        ;;
        update)
        UPDATE=true
        shift
        ;;
        *)
        echo -n "Invalid arguments, please check the usage doc"
        help
        exit 1
        ;;
    esac
done

if [[ "$GA_MODE" == true && "$BUILD_TYPE" == "nightly" ]]; then
  echo "Error: --ga/--stable and --nightly flags are mutually exclusive"
  exit 1
fi

if [[ "$GA_MODE" == true && "$ODH_MODE" == true ]]; then
  echo "Error: --ga/--stable flag is not compatible with --odh mode"
  exit 1
fi

if [[ -z $SKOPEO_TOKEN_FILE_PATH ]]; then
    SKOPEO_TOKEN_FILE_PATH=~/.ssh/.rhoai_quay_ro_token
fi

# Set configuration based on ODH mode
if [[ "$ODH_MODE" == true ]]; then
    BUILD_CONFIG_REPO=https://github.com/opendatahub-io/ODH-Build-Config
    QUAY_BASE_URL="quay.io/opendatahub"
    FBC_QUAY_REPO="opendatahub-operator-catalog"
    BUNDLE_QUAY_REPO="opendatahub-operator-bundle"
else
    # Keep existing RHOAI configuration
    BUILD_CONFIG_REPO=https://github.com/red-hat-data-services/RHOAI-Build-Config
    QUAY_BASE_URL="quay.io/rhoai"
    FBC_QUAY_REPO="rhoai-fbc-fragment"
    BUNDLE_QUAY_REPO="odh-operator-bundle"
fi

if [[ $CONFIGURE == "true" ]]
then
  auth=$(cat $SKOPEO_TOKEN_FILE_PATH | base64 -d | tr -d '\n\r')
  IFS=':' read -a parts <<< "$auth"
  if [[ ${#parts[@]} -ne 2 ]]; then
    echo "Error: Invalid token format"
    exit 1
  fi
  skopeo login -u "${parts[0]}" -p "${parts[1]}" "$QUAY_BASE_URL"
  exit
fi

if [[ $UPDATE == "true" ]]
then
  git_url=git@github.com:red-hat-data-services/rhods-devops-infra.git
  current_script_path=$(realpath $0)
  current_dir=$(dirname "${current_script_path}")
  temp=$(mktemp -d)
  cd $temp
  git config --global init.defaultBranch main
  git init
  git remote add origin $git_url
  git config core.sparseCheckout true
  git config core.sparseCheckoutCone false
  echo "tools/tracer" >> .git/info/sparse-checkout
  git fetch --depth=1 origin main
  git checkout main
  cp tools/tracer/tracer.sh "${current_script_path}"
  echo "Tracer is updated successfully!"
  cd $current_dir
  rm -rf $temp
  exit
fi

if [[ -z $INPUT_VERSION ]]; then
  if [[ "$ODH_MODE" == true ]]; then
      # ODH defaults to odh-stable tag
      TAG="odh-stable"
  else
      # GA branches (rhoai-X.Y) take precedence over EA branches (rhoai-X.Y-ea.N)
      # for the same minor version, but a newer EA (e.g. 3.5-ea.1) outranks an
      # older GA (e.g. 3.4). We achieve this by appending a high-value suffix to
      # GA branches so they sort above any EA branch for the same version.
      TAG=$(git ls-remote --heads $BUILD_CONFIG_REPO \
        | grep -E 'refs/heads/rhoai-[0-9]+\.[0-9]+(-ea\.[0-9]+)?$' \
        | awk -F'/' '{print $NF}' \
        | awk '{ga=$0; sub(/$/, "-ga.999", ga); if ($0 !~ /-ea\./) print ga"|"$0; else print $0"|"$0}' \
        | sort -t'|' -k1,1V | tail -1 | cut -d'|' -f2 || true)
  fi
else
  if [[ "$ODH_MODE" == true ]]; then
    # Throw an error if the INPUT_VERSION is not set to 'odh-stable'
    if [[ -n "$INPUT_VERSION" ]]; then
      echo "Error: In ODH mode, the catalog image with the 'odh-stable' is used. Specifying a version other than 'odh-stable' is not supported."
      exit 1
    fi
    TAG="odh-stable"
  else
    # RHOAI mode - existing logic

    # cleanup INPUT_VERSION
    INPUT_VERSION=$(echo $INPUT_VERSION | tr '[A-Z]' '[a-z]' )
    if [[ "$INPUT_VERSION" == v* ]]; then INPUT_VERSION=$(echo $INPUT_VERSION | tr -d 'v'); fi

    if [[ "$INPUT_VERSION" != rhoai* ]]; then 
      TAG="rhoai-${INPUT_VERSION}"
    else
      TAG=$INPUT_VERSION
    fi
  fi
fi

if [[ -z $IMAGE ]]
then
  IMAGE_TYPE=$(echo $IMAGE_TYPE | tr '[a-z]' '[A-Z]')
  BUILD_TYPE=$(echo $BUILD_TYPE | tr '[a-z]' '[A-Z]')
  IMAGE_MANIFEST=
  QUAY_REPO=

  if [[ $IMAGE_TYPE == "FBC" ]]; then 
    QUAY_REPO=$FBC_QUAY_REPO
  elif [[ $IMAGE_TYPE == "BUNDLE" ]]; then 
    QUAY_REPO=$BUNDLE_QUAY_REPO
  fi

  if [[ -n $DIGEST ]]; then
    if [[ "$DIGEST" != sha256* ]]; then DIGEST="sha256:${DIGEST}"; fi
    IMAGE_MANIFEST="@$DIGEST"
  elif [[ -n $TAG ]]; then

    # Add nightly suffix
    if [[ "$(echo "$BUILD_TYPE" | tr '[:upper:]' '[:lower:]')" == "nightly" ]]; then TAG="${TAG}-nightly"; fi

    # For GA mode, resolve the vX.Y.Z-ga tag via skopeo inspect.
    # If a full X.Y.Z version is given, check that exact tag.
    # If only X.Y (minor) is given, probe patch versions upward from 0;
    # the highest existing tag wins.
    if [[ "$GA_MODE" == true ]]; then
      VERSION_PART=${TAG#rhoai-}
      GA_TAG=""
      if [[ "$VERSION_PART" =~ ^[0-9]+\.[0-9]+\.[0-9]+ ]]; then
        candidate="v${VERSION_PART}-ga"
        if skopeo inspect --no-tags "docker://${QUAY_BASE_URL}/${QUAY_REPO}:${candidate}" --override-arch amd64 --override-os linux >/dev/null 2>&1; then
          GA_TAG="$candidate"
        fi
        if [[ -z "$GA_TAG" ]]; then
          echo "Error: GA-tagged image v${VERSION_PART}-ga not found in ${QUAY_BASE_URL}/${QUAY_REPO}"
          exit 1
        fi
      else
        for patch in $(seq 0 50); do
          candidate="v${VERSION_PART}.${patch}-ga"
          if skopeo inspect --no-tags "docker://${QUAY_BASE_URL}/${QUAY_REPO}:${candidate}" --override-arch amd64 --override-os linux >/dev/null 2>&1; then
            GA_TAG="$candidate"
          else
            break
          fi
        done
        if [[ -z "$GA_TAG" ]]; then
          echo "Error: No GA-tagged image found matching v${VERSION_PART}.*-ga in ${QUAY_BASE_URL}/${QUAY_REPO}"
          exit 1
        fi
      fi
      TAG="$GA_TAG"
    fi

    IMAGE_MANIFEST=":$TAG"
  fi
  IMAGE_URI="docker://${QUAY_BASE_URL}/${QUAY_REPO}${IMAGE_MANIFEST}"
  FULL_IMAGE_URI_WITH_DIGEST="docker://${QUAY_BASE_URL}/${QUAY_REPO}"


else
  IMAGE_URI=${IMAGE/http:\/\//}
  IMAGE_URI=${IMAGE_URI/https:\/\//}
  IMAGE_URI=${IMAGE_URI/docker:\/\//}

  # strip the tag if it exists in tandem with a sha sum
  IMAGE_URI=$(echo $IMAGE_URI | sed -e 's/:.*@/@/g')
  if [[ "$IMAGE_URI" != docker* ]]; then IMAGE_URI="docker://${IMAGE_URI}"; fi
  FULL_IMAGE_URI_WITH_DIGEST=$IMAGE_URI

  if [[ "$ODH_MODE" == true && "$IMAGE_URI" =~ quay\.io/rhoai ]]; then
    echo "Error: Conflict between ODH mode and RHOAI quay URI '$IMAGE_URI'"
    exit 1
  fi
   
fi

if [[ -n $IMAGE_URI ]]
then
  META=$(skopeo inspect --no-tags "${IMAGE_URI}" --override-arch amd64 --override-os linux)
  NAME=$(echo $META | jq -r .Name)
  IFS='/' read -a parts <<< "$NAME"
  CURRENT_COMPONENT="${parts[2]}"
  DIGEST=$(echo $META | jq -r .Digest)

  labels=$(echo $META | jq .Labels)

  FULL_IMAGE_URI_WITH_DIGEST="${NAME}@${DIGEST}"

  # Check if FULL_IMAGE_URI_WITH_DIGEST is properly formed
  if [[ "$FULL_IMAGE_URI_WITH_DIGEST" =~ ^[[:space:]]*@[[:space:]]*$ ]]; then
    echo "Error: Unable to construct proper image URI with digest"
    exit 1
  fi

  BUILD_DATE=$(echo $labels | jq -r '."build-date"')
  VERSION=$(echo $labels | jq -r '."version"')

  TEXT_OUTPUT+=$(printf "%-55s %s" "Image-URI"     "$FULL_IMAGE_URI_WITH_DIGEST")$'\n'
  TEXT_OUTPUT+=$(printf "%-55s %s" "Build-Date"    "$BUILD_DATE")$'\n'

  if [[ "$ODH_MODE" == true ]]; then
    FBC_GIT_COMMIT=$(echo $labels | jq -r '."git.commit"')
    URL="https://raw.githubusercontent.com/opendatahub-io/ODH-Build-Config/${FBC_GIT_COMMIT}/bundle/bundle-patch.yaml"
    VERSION=$(curl -sL "$URL" | yq '.patch.version')
    TEXT_OUTPUT+=$(printf "%-55s %s" "ODH-Version" "$VERSION")
  else
    TEXT_OUTPUT+=$(printf "%-55s %s" "RHOAI-Version" "$VERSION")
  fi

  if [[ "$OUTPUT_FORMAT" == "text" ]]; then
    echo -e "${TEXT_OUTPUT}\n"
  fi

  if [[ "$SHOW_COMMITS" == "true" ]]
  then
    # Extract current component output
    # Ensure CURRENT_COMPONENT_OUTPUT has a newline at the end
    CURRENT_COMPONENT_OUTPUT=$(echo $labels | jq -r --arg NAME "$CURRENT_COMPONENT" '$NAME + ": " + ."git.url" + "/tree/" + ."git.commit"')
    CURRENT_COMPONENT_OUTPUT="${CURRENT_COMPONENT_OUTPUT}\n"

    # Initialize output variables
    BUNDLE_COMMIT_OUTPUT=""
    OPERATOR_COMMIT_OUTPUT=""
    MANIFESTS_CONFIG_OUTPUT=""
    ERROR_MESSAGE=""
    SHA_FOR_LOOKUP=""
    SKIP_SUBCOMPONENT_SEARCH=false

    # Extract bundle git information for FBC/catalog images only
    if [[ "$CURRENT_COMPONENT" == "rhoai-fbc-fragment" || "$CURRENT_COMPONENT" == "opendatahub-operator-catalog" ]]; then
      # Create bundle component name based on mode
      if [[ "$ODH_MODE" == true ]]; then
          BUNDLE_COMPONENT_NAME="opendatahub-operator-bundle"
      else
          BUNDLE_COMPONENT_NAME="odh-operator-bundle"
      fi

      # Use dynamic bundle component name in jq filters
      BUNDLE_GIT_COMMIT=$(echo $labels | jq -r --arg bundle_name "$BUNDLE_COMPONENT_NAME" '.[$bundle_name + ".git.commit"] // empty')
      BUNDLE_GIT_URL=$(echo $labels | jq -r --arg bundle_name "$BUNDLE_COMPONENT_NAME" '.[$bundle_name + ".git.url"] // empty')
      
      # Terminate if bundle commit/url is blank or null, otherwise set the variable
      if [[ -z "$BUNDLE_GIT_COMMIT" || -z "$BUNDLE_GIT_URL" || "$BUNDLE_GIT_COMMIT" == "null" || "$BUNDLE_GIT_URL" == "null" ]]; then
        echo "Error: Bundle git commit or URL information is missing for $BUNDLE_COMPONENT_NAME"
        exit 1
      else
        BUNDLE_COMMIT_OUTPUT="$BUNDLE_COMPONENT_NAME: ${BUNDLE_GIT_URL}/tree/${BUNDLE_GIT_COMMIT}\n"
      fi
    elif [[ "$CURRENT_COMPONENT" != "opendatahub-operator-bundle" && "$CURRENT_COMPONENT" != "odh-operator-bundle" ]]; then
      # when the current component is neither a bundle nor a catalog, skip the subcomponent search and just print the result
      SKIP_SUBCOMPONENT_SEARCH=true
    fi
    
    COMMIT_OUTPUT="${CURRENT_COMPONENT_OUTPUT}"

    if [[ "$SKIP_SUBCOMPONENT_SEARCH" == false ]]; then
      # Create operator component name based on mode
      if [[ "$ODH_MODE" == true ]]; then
          OPERATOR_COMPONENT_NAME="opendatahub-operator"
      else
          # Search for RHOAI operator component name using pattern matching
          OPERATOR_COMPONENT_NAME=$(echo $labels | jq -r 'keys[] | select(test("^odh-rhel\\d+-operator\\.git\\.commit"))' | sed 's/\.git\.commit$//' | head -1)
          if [[ -z "$OPERATOR_COMPONENT_NAME" ]]; then
              echo "Error: No RHOAI operator component found matching pattern 'odh-rhel*-operator.git.commit'"
              exit 1
          fi
      fi

      # Extract operator commit SHA label
      OPERATOR_GIT_COMMIT=$(echo $labels | jq -r --arg bundle_name "$OPERATOR_COMPONENT_NAME" '.[$bundle_name + ".git.commit"] // empty')
      OPERATOR_GIT_URL=$(echo $labels | jq -r --arg bundle_name "$OPERATOR_COMPONENT_NAME" '.[$bundle_name + ".git.url"] // empty')

      if [[ -n "$OPERATOR_GIT_COMMIT" && -n "$OPERATOR_GIT_URL" && "$OPERATOR_GIT_COMMIT" != "null" && "$OPERATOR_GIT_URL" != "null" ]]; then
        OPERATOR_COMMIT_OUTPUT="$OPERATOR_COMPONENT_NAME: ${OPERATOR_GIT_URL}/tree/${OPERATOR_GIT_COMMIT}\n"
      fi

      COMMIT_OUTPUT="${COMMIT_OUTPUT}${BUNDLE_COMMIT_OUTPUT}${OPERATOR_COMMIT_OUTPUT}"
      # Determine SHA for lookup and build operator commit output
      # 
      if [[ "$ODH_MODE" == true ]]; then
        BUILD_METADATA_URL=$(echo $labels | jq -r '."build-metadata-url" // empty')
    
        # Exit with error if the OPERATOR_IMAGE does not exist or its value is bad
        if [[ -z "$BUILD_METADATA_URL" || "$BUILD_METADATA_URL" == "empty" || "$BUILD_METADATA_URL" == "null" ]]; then
          echo "Error: build-metadata-url label is missing or invalid"
          exit 1
        fi

        MANIFESTS_CONFIG_URL="${BUILD_METADATA_URL}/manifests-config.yaml"
        MANIFESTS_CONFIG_URL=$(echo "${MANIFESTS_CONFIG_URL}" | sed 's|^https://github.com|https://raw.githubusercontent.com|' | sed 's|tree|refs/heads|')

      else
        MANIFESTS_CONFIG_URL="https://raw.githubusercontent.com/red-hat-data-services/rhods-operator/${OPERATOR_GIT_COMMIT}/build/manifests-config.yaml"
      fi

      # Add error validation on the result of the curl command as well as the result of the yq command
      MANIFESTS_CONFIG_RAW=$(curl -sL "$MANIFESTS_CONFIG_URL")
      if [[ $? -ne 0 || -z "$MANIFESTS_CONFIG_RAW" ]]; then
        echo -e "$COMMIT_OUTPUT"
        echo "Error: Failed to fetch manifests config from: $MANIFESTS_CONFIG_URL"
        exit 1
      fi

      MANIFESTS_CONFIG_OUTPUT=$(echo "$MANIFESTS_CONFIG_RAW" | yq e '
        [
          (.map | to_entries[] | select(.key != "notebooks")),
          (.additional_meta | to_entries[])
        ]
        | flatten | sort_by(.key)
        | .[] | .key + ": " + .value."git.url" + "/tree/" + .value."git.commit"
      ')
      if [[ $? -ne 0 || -z "$MANIFESTS_CONFIG_OUTPUT" ]]; then
        echo -e "$COMMIT_OUTPUT"
        echo "Error: Failed to parse manifests config YAML from: $MANIFESTS_CONFIG_URL"
        exit 1
      fi
      
    fi

    # Assemble final commit output
    COMMIT_OUTPUT="${COMMIT_OUTPUT}${MANIFESTS_CONFIG_OUTPUT}"

    # Format and display based on output format
    if [[ "$OUTPUT_FORMAT" == "json" || "$OUTPUT_FORMAT" == "yaml" ]]; then
      STRUCTURED_JSON=$(echo -e "${COMMIT_OUTPUT}" | awk -F': ' '
        NF>=2 && $1 != "" {
          component=$1
          url=$2
          gsub(/^[ \t]+|[ \t]+$/, "", component)
          gsub(/^[ \t]+|[ \t]+$/, "", url)
          if (component != "" && url != "") {
            print component "\t" url
          }
        }
      ' | jq -R -s --arg image_uri "$FULL_IMAGE_URI_WITH_DIGEST" \
                    --arg build_date "$BUILD_DATE" \
                    --arg version "$VERSION" '
        split("\n") | map(select(length > 0)) | map(split("\t")) |
        {
          "image_uri": $image_uri,
          "build_date": $build_date,
          "version": $version,
          "components": (
            map(select(length == 2)) | reduce .[] as $item (
              {};
              .[$item[0]] = {
                "git.url": ($item[1] | split("/tree/") | .[0]),
                "git.commit": ($item[1] | split("/tree/") | .[1] // "")
              }
            )
          )
        }
      ')

      if [[ "$OUTPUT_FORMAT" == "json" ]]; then
        echo "$STRUCTURED_JSON"
      elif [[ "$OUTPUT_FORMAT" == "yaml" ]]; then
        echo "$STRUCTURED_JSON" | yq -P '.'
      fi
    else
      echo -e "${COMMIT_OUTPUT}" | awk -F': ' '{printf "%-50s\t%s\n", $1, $2}'
    fi
  fi
  
  if [[ -n $SEARCH_PARAM ]] 
  then
    
    SEARCH_REPO=$(echo $SEARCH_PARAM | grep -o '^.*/' | sed 's|/$||' )
    SEARCH_SHA=$(echo $SEARCH_PARAM | sed 's|^.*/||' | awk '{print tolower($0)}')
    COMPONENTS=$( echo $labels | jq -r 'keys[] | select(test(".*\\.git\\.url"))' |  sed 's/\.git\.url//' )
    FOUND_RESULT=false
    FOUND_MATCHING_REPO=false
    QUERIES=
    REPOS=
    for component in $COMPONENTS
    do  
      url_key="$component.git.url"; commit_key="$component.git.commit";
      
      URL=$(echo $labels | jq  --arg url_key "$url_key" -r '"\(.[$url_key])"')
      ORG_REPO=$( echo $URL | sed 's|^https://[^/]*/||' | sed 's/.git$//' )
      COMMIT=$(echo $labels | jq  --arg commit_key "$commit_key" -r '"\(.[$commit_key])"')
      if [[ -z $SEARCH_REPO || $ORG_REPO =~ $SEARCH_REPO ]] 
      then
        FOUND_MATCHING_REPO=true
      else
        continue
      fi 
 
      API_URL="https://api.github.com/repos/${ORG_REPO}/compare/${COMMIT}...${SEARCH_SHA}"
      if [[ -n $(echo "$QUERIES" |  grep "$API_URL" ) ]]
      then
        # echo "skipped $API_URL" 
        continue
      fi  
      QUERIES+=" $API_URL"
      REPOS+="\n$ORG_REPO"  
      API_RESPONSE=$(curl -s ${API_URL} )
      if [[ $? -ne 0 ]]
      then
        echo "error with github API call"
        echo "component: $component"
        echo "repo: $URL"
        echo "error message:"
        echo $API_RESPONSE
        exit 1
      fi
      SEARCH_RESULT=$( echo $API_RESPONSE | jq -r '.status')
      
      if [[ "$SEARCH_RESULT" == "behind" || "$SEARCH_RESULT" == "identical" ]] 
      then
        FOUND_COMMIT=$(echo $API_RESPONSE | jq '.merge_base_commit')
        echo -e "\nFound commit SHA starting with '$SEARCH_SHA' in $ORG_REPO:" 
        echo -e "----"
        echo -e "component\t $component"
        echo -e "source\t\t ${URL}/tree/${COMMIT}"
        echo -e "----"
        echo -e "commit\t\t $( echo $FOUND_COMMIT | jq -r '.html_url')"
        echo -e "date\t\t $( echo $FOUND_COMMIT | jq -r '.commit.author.date' )"
        echo -e "author\t\t $( echo $FOUND_COMMIT | jq -r '.commit.author.name' )"
        echo "message: "
        echo "$FOUND_COMMIT" | jq -r '.commit.message' 
        echo "----"
        FOUND_RESULT=true
      fi
    done 
    if [[ "$FOUND_MATCHING_REPO" == "false" ]]
    then
      echo "Did not find any components with a source repo matching '$SEARCH_REPO'"
    elif [[ "$FOUND_RESULT" == "false" ]]
    then
      echo -e "\nCommit SHA starting with $SEARCH_SHA was not found in: "
      echo -e "$REPOS"
    fi
  fi

else
  echo "Image is not found"
fi
