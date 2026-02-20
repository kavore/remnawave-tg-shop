import aiohttp
import logging
import json
import re
from typing import Optional, List, Dict, Any
from datetime import datetime, timedelta, timezone
import asyncio
from urllib.parse import urlencode

from sqlalchemy.ext.asyncio import AsyncSession

from config.settings import Settings
from db.dal import panel_sync_dal
from db.models import PanelSyncStatus


class PanelApiService:

    def __init__(self, settings: Settings):
        self.settings = settings
        self.base_url = settings.PANEL_API_URL
        self.api_key = settings.PANEL_API_KEY
        self._session: Optional[aiohttp.ClientSession] = None
        self.default_client_ip = "127.0.0.1"

    async def __aenter__(self):
        """Context manager entry"""
        return self

    async def __aexit__(self, exc_type, exc_val, exc_tb):
        """Context manager exit - automatically close session"""
        await self.close_session()

    async def _get_session(self) -> aiohttp.ClientSession:
        if self._session is None or self._session.closed:
            timeout = aiohttp.ClientTimeout(total=30)
            self._session = aiohttp.ClientSession(timeout=timeout)
        return self._session

    async def close_session(self):
        if self._session and not self._session.closed:
            await self._session.close()
            self._session = None
            logging.debug("Panel API service HTTP session closed.")

    async def close(self):
        """Alias for close_session for API consistency."""
        await self.close_session()

    async def _prepare_headers(self) -> Dict[str, str]:
        headers = {
            "Content-Type": "application/json",
            "Accept": "application/json",
            "X-Forwarded-Proto": "https",
            "X-Forwarded-For": self.default_client_ip,
            "X-Real-IP": self.default_client_ip,
        }
        if self.api_key:
            headers["Authorization"] = f"Bearer {self.api_key}"
        return headers

    @staticmethod
    def _sanitize_payload_for_log(payload: Any) -> Any:
        if isinstance(payload, dict):
            redacted: Dict[str, Any] = {}
            for key, value in payload.items():
                lowered = str(key).lower()
                if any(mask_key in lowered for mask_key in (
                    "token",
                    "secret",
                    "password",
                    "authorization",
                    "api_key",
                    "apikey",
                    "key",
                )):
                    redacted[key] = "***"
                else:
                    redacted[key] = PanelApiService._sanitize_payload_for_log(value)
            return redacted
        if isinstance(payload, list):
            return [PanelApiService._sanitize_payload_for_log(item) for item in payload]
        return payload

    async def _request(self,
                       method: str,
                       endpoint: str,
                       log_full_response: bool = False,
                       **kwargs) -> Optional[Dict[str, Any]]:
        if not self.base_url:
            logging.error(
                "Panel API URL (PANEL_API_URL) not configured in settings.")
            return {
                "error": True,
                "status_code": 0,
                "message": "Panel API URL not configured."
            }

        aiohttp_session = await self._get_session()
        headers = await self._prepare_headers()

        url_for_request = f"{self.base_url.rstrip('/')}/{endpoint.lstrip('/')}"

        current_params = kwargs.get("params")
        url_with_params_for_log = url_for_request
        if current_params:
            try:
                url_with_params_for_log += "?" + urlencode(current_params)
            except Exception as exc:
                logging.debug("Failed to encode params for panel API log URL: %s", exc)

        json_payload_for_log = kwargs.get('json') if method.upper() in [
            "POST", "PATCH", "PUT"
        ] else None
        log_prefix = f"Panel API Req: {method.upper()} {url_with_params_for_log}"
        if json_payload_for_log:
            try:
                sanitized_payload = self._sanitize_payload_for_log(json_payload_for_log)
                payload_str = json.dumps(sanitized_payload)
                log_prefix += f" | Payload: {payload_str[:300]}{'...' if len(payload_str) > 300 else ''}"
            except Exception:
                log_prefix += " | Payload: <unavailable>"
        try:
            async with aiohttp_session.request(method.upper(),
                                               url_for_request,
                                               headers=headers,
                                               **kwargs) as response:
                response_status = response.status
                response_text = await response.text()

                log_suffix = f"| Status: {response_status}"

                should_log_full_body = bool(log_full_response and self.settings.LOG_LEVEL == "DEBUG")
                if should_log_full_body or not (200 <= response_status < 300):
                    try:
                        parsed_json_for_log = json.loads(response_text)
                        pretty_response_text = json.dumps(parsed_json_for_log,
                                                          indent=2,
                                                          ensure_ascii=False)
                        logging.info(
                            f"{log_prefix} {log_suffix} | Full Response Body:\n{pretty_response_text}"
                        )
                    except json.JSONDecodeError:
                        logging.info(
                            f"{log_prefix} {log_suffix} | Full Response Text (not JSON):\n{response_text[:2000]}{'...' if len(response_text) > 2000 else ''}"
                        )
                else:
                    logging.debug(
                        f"{log_prefix} {log_suffix} | OK. Response Body Preview: {response_text[:200]}{'...' if len(response_text) > 200 else ''}"
                    )

                if 200 <= response_status < 300:
                    try:
                        if 'application/json' in response.headers.get(
                                'Content-Type', '').lower():
                            data = json.loads(response_text)
                            return data
                        else:
                            return {
                                "status": "success",
                                "code": response_status,
                                "data_text": response_text
                            }
                    except json.JSONDecodeError as e_json_ok:
                        logging.error(
                            f"{log_prefix} {log_suffix} | OK but JSON Parse Error. Error: {e_json_ok}. Body was logged above."
                        )
                        return {
                            "status": "success_parse_error",
                            "code": response_status,
                            "data_text": response_text,
                            "parse_error": str(e_json_ok)
                        }
                else:
                    error_details = {
                        "message":
                        f"Request failed with status {response_status}",
                        "raw_response_text": response_text
                    }
                    try:
                        if 'application/json' in response.headers.get(
                                'Content-Type', '').lower():
                            error_json_data = json.loads(response_text)
                            error_details.update(error_json_data)
                    except json.JSONDecodeError:
                        pass
                    return {
                        "error": True,
                        "status_code": response_status,
                        "details": error_details
                    }

        except aiohttp.ClientConnectorError as e:
            logging.error(
                f"Panel API ClientConnectorError to {url_for_request}: {e}")
            return {
                "error": True,
                "status_code": -1,
                "message": f"Connection error: {str(e)}"
            }
        except aiohttp.ClientError as e:
            logging.error(f"Panel API ClientError to {url_for_request}: {e}")
            return {
                "error": True,
                "status_code": -2,
                "message": f"Client error: {str(e)}"
            }
        except asyncio.TimeoutError:
            logging.error(f"Panel API request to {url_for_request} timed out.")
            return {
                "error": True,
                "status_code": -3,
                "message": "Request timed out"
            }
        except Exception as e:
            logging.error(
                f"Unexpected Panel API request error to {url_for_request}: {e}",
                exc_info=True)
            return {
                "error": True,
                "status_code": -4,
                "message": f"Unexpected error: {str(e)}"
            }

    async def get_all_panel_users(
            self,
            page_size: int = 100,
            log_responses: bool = False) -> Optional[List[Dict[str, Any]]]:
        all_users = []
        start_offset = 0
        while True:
            params = {"size": page_size, "start": start_offset}
            response_data = await self._request(
                "GET",
                "/users",
                params=params,
                log_full_response=log_responses)

            if not response_data or response_data.get("error"):
                logging.error(
                    f"Failed to fetch panel users batch (start: {start_offset}). Response: {response_data}"
                )
                return None
            users_batch = response_data.get("response", {}).get("users", [])
            if not users_batch: break
            all_users.extend(users_batch)
            if len(users_batch) < page_size: break
            start_offset += page_size
            await asyncio.sleep(0.1)
        logging.info(f"Fetched {len(all_users)} users from panel API.")
        return all_users

    async def get_user_by_uuid(
            self,
            user_uuid: str,
            log_response: bool = True) -> Optional[Dict[str, Any]]:
        endpoint = f"/users/{user_uuid}"
        full_response = await self._request("GET",
                                            endpoint,
                                            log_full_response=log_response)
        if full_response and not full_response.get(
                "error") and "response" in full_response:
            return full_response.get("response")

        return None

    async def get_user(
        self,
        *,
        uuid: Optional[str] = None,
        telegram_id: Optional[int] = None,
        username: Optional[str] = None,
        email: Optional[str] = None,
        log_response: bool = True,
    ) -> Optional[Dict[str, Any]]:
        if uuid:
            return await self.get_user_by_uuid(uuid, log_response=log_response)

        users = await self.get_users_by_filter(
            telegram_id=telegram_id,
            username=username,
            email=email,
            log_response=log_response,
        )
        if users:
            return users[0]
        return None

    async def get_users_by_filter(
            self,
            telegram_id: Optional[int] = None,
            username: Optional[str] = None,
            email: Optional[str] = None,
            log_response: bool = True) -> Optional[List[Dict[str, Any]]]:

        response_data = None
        filter_used_log = "No filter specified"

        if telegram_id is not None:
            filter_used_log = f"telegramId={telegram_id}"
            endpoint = f"/users/by-telegram-id/{telegram_id}"
            response_data = await self._request("GET",
                                                endpoint,
                                                log_full_response=log_response)

            if response_data and not response_data.get(
                    "error") and "response" in response_data and isinstance(
                        response_data["response"], list):
                return response_data["response"]
            elif response_data and response_data.get("errorCode") == "A062":
                logging.info(
                    f"Panel API: Users not found for {filter_used_log}")
                return []

        elif username is not None:
            filter_used_log = f"username={username}"
            endpoint = f"/users/by-username/{username}"
            response_data = await self._request("GET",
                                                endpoint,
                                                log_full_response=log_response)

            if response_data and not response_data.get(
                    "error") and "response" in response_data and isinstance(
                        response_data["response"], dict):
                return [response_data["response"]]
            elif response_data and response_data.get("errorCode") == "A062":
                logging.info(
                    f"Panel API: User not found for {filter_used_log}")
                return []

        elif email is not None:
            filter_used_log = f"email={email}"
            endpoint = f"/users/by-email/{email}"
            response_data = await self._request("GET",
                                                endpoint,
                                                log_full_response=log_response)

            if response_data and not response_data.get(
                    "error") and "response" in response_data and isinstance(
                        response_data["response"], list):
                return response_data["response"]
            elif response_data and response_data.get("errorCode") == "A062":
                logging.info(
                    f"Panel API: Users not found for {filter_used_log}")
                return []

        if not telegram_id and not username and not email:
            logging.warning(
                "get_users_by_filter called without any specific filter criteria."
            )
            return []

        logging.error(
            f"Failed to fetch panel users with filter ({filter_used_log}). Last API response: {response_data if not log_response else '(logged above)'}"
        )
        return None

    async def create_panel_user(
            self,
            username_on_panel: str,
            telegram_id: Optional[int] = None,
            email: Optional[str] = None,
            default_expire_days: int = 1,
            default_traffic_limit_bytes: int = 0,
            default_traffic_limit_strategy: str = "NO_RESET",
            hwid_device_limit: Optional[int] = None,
            specific_squad_uuids: Optional[List[str]] = None,
            external_squad_uuid: Optional[str] = None,
            description: Optional[str] = None,
            tag: Optional[str] = None,
            status: str = "ACTIVE",
            log_response: bool = True) -> Optional[Dict[str, Any]]:

        username_is_valid = (
            3 <= len(username_on_panel) <= 36
            and re.match(r"^[A-Za-z0-9_-]+$", username_on_panel) is not None
        )
        if not username_is_valid:
            msg = f"Panel username '{username_on_panel}' does not meet panel requirements."
            logging.error(msg)
            return {
                "error": True,
                "status_code": 400,
                "message": msg,
                "errorCode": "VALIDATION_ERROR_USERNAME"
            }

        now = datetime.now(timezone.utc)
        expire_at_dt = now + timedelta(days=default_expire_days)
        expire_at_iso = expire_at_dt.isoformat(
            timespec='milliseconds').replace('+00:00', 'Z')

        payload: Dict[str, Any] = {
            "username": username_on_panel,
            "status": status.upper(),
            "expireAt": expire_at_iso,
            "trafficLimitStrategy": default_traffic_limit_strategy.upper(),
            "trafficLimitBytes": default_traffic_limit_bytes,
        }
        hwid_limit_value = hwid_device_limit
        if hwid_limit_value is None:
            hwid_limit_value = self.settings.USER_HWID_DEVICE_LIMIT
        if hwid_limit_value is not None:
            try:
                hwid_limit_int = int(hwid_limit_value)
                if hwid_limit_int >= 0:
                    payload["hwidDeviceLimit"] = hwid_limit_int
            except (TypeError, ValueError):
                logging.warning(
                    f"Ignoring invalid HWID device limit '{hwid_limit_value}' while creating panel user '{username_on_panel}'."
                )
        if specific_squad_uuids:
            payload["activeInternalSquads"] = specific_squad_uuids
        if external_squad_uuid:
            payload["externalSquadUuid"] = external_squad_uuid
        if telegram_id is not None: payload["telegramId"] = telegram_id
        if email: payload["email"] = email
        if description: payload["description"] = description
        if tag: payload["tag"] = tag

        response = await self._request("POST",
                                       "/users",
                                       json=payload,
                                       log_full_response=log_response)
        if response and not response.get("error") and "response" in response:
            logging.info(
                f"Panel user '{username_on_panel}' created successfully (UUID: {response.get('response',{}).get('uuid')})."
            )
            return response

        logging.error(
            "Failed to create panel user '%s'. Payload: %s, Response: %s",
            username_on_panel,
            self._sanitize_payload_for_log(payload),
            response if not log_response else "(full response logged above)",
        )
        return response

    async def update_user_details_on_panel(
            self,
            user_uuid: str,
            update_payload: Dict[str, Any],
            log_response: bool = True) -> Optional[Dict[str, Any]]:
        if 'uuid' not in update_payload:
            update_payload['uuid'] = user_uuid

        full_response = await self._request("PATCH",
                                            "/users",
                                            json=update_payload,
                                            log_full_response=log_response)
        if full_response and not full_response.get(
                "error") and "response" in full_response:
            logging.info(f"User {user_uuid} details updated on panel.")
            return full_response.get("response")

        logging.error(
            "Failed to update user %s details on panel. Payload: %s, Response: %s",
            user_uuid,
            self._sanitize_payload_for_log(update_payload),
            full_response if not log_response else "(logged above)",
        )
        return None

    async def update_user_status_on_panel(self,
                                          user_uuid: str,
                                          enable: bool,
                                          log_response: bool = True) -> bool:
        action = "enable" if enable else "disable"
        endpoint = f"/users/{user_uuid}/actions/{action}"
        response_data = await self._request("POST",
                                            endpoint,
                                            log_full_response=log_response)

        if response_data and not response_data.get(
                "error") and "response" in response_data:
            actual_status = response_data.get("response", {}).get("status")
            expected_status = "ACTIVE" if enable else "DISABLED"
            if actual_status == expected_status:
                logging.info(
                    f"User {user_uuid} status on panel successfully set to {action} (Actual: {actual_status})."
                )
                return True
            else:
                logging.warning(
                    f"User {user_uuid} status on panel action '{action}' called, but final status is '{actual_status}'."
                )
                return False

        logging.error(
            f"Failed to {action} user {user_uuid} on panel. Response: {response_data if not log_response else '(logged above)'}"
        )
        return False

    async def delete_user_from_panel(self,
                                     user_uuid: str,
                                     log_response: bool = True) -> bool:
        """Delete a user from the panel. Treat not-found as already deleted."""
        endpoint = f"/users/{user_uuid}"
        response_data = await self._request(
            "DELETE", endpoint, log_full_response=log_response
        )

        if not response_data:
            logging.error(
                f"Panel API delete_user_from_panel returned no data for user {user_uuid}."
            )
            return False

        if response_data.get("error"):
            details = response_data.get("details") or {}
            error_code = details.get("errorCode") or response_data.get("errorCode")
            if error_code in {"A062", "A040"}:
                logging.info(
                    f"Panel user {user_uuid} already absent (errorCode {error_code}). Treating as deleted."
                )
                return True
            logging.error(
                f"Failed to delete user {user_uuid} on panel. Response: {response_data}"
            )
            return False

        logging.info(f"Panel user {user_uuid} deleted successfully.")
        return True

    async def get_subscription_link(
            self,
            short_uuid_or_sub_uuid: str,
            client_type: Optional[str] = None) -> Optional[str]:
        if not self.settings.PANEL_API_URL:
            logging.error(
                "PANEL_API_URL not set, cannot generate subscription link.")
            return None
        base_sub_url = f"{self.settings.PANEL_API_URL.rstrip('/')}/sub/{short_uuid_or_sub_uuid}"
        if client_type:
            return f"{base_sub_url}/{client_type.lower()}"
        return base_sub_url

    async def get_user_devices(self, user_uuid: str) -> Optional[List[Dict[str, Any]]]:
        endpoint = f"/hwid/devices/{user_uuid}"
        response_data = await self._request("GET", endpoint, log_full_response=False)
        if response_data and not response_data.get("error") and "response" in response_data:
            return response_data.get("response")
        logging.error(
            f"Failed to get user devices for user {user_uuid}. Response: {response_data}"
        )
        return None

    async def disconnect_device(self, user_uuid: str, hwid: str) -> bool:
        endpoint = f"/hwid/devices/delete"
        payload = {
            "userUuid": user_uuid,
            "hwid": hwid
        }
        response_data = await self._request("POST", endpoint, json=payload, log_full_response=False)
        if response_data and not response_data.get("error") and "response" in response_data:
            return True
        logging.error(
            f"Failed to disconnect device {hwid} for user {user_uuid}. Payload: {payload}, Response: {response_data}"
        )
        return False

    async def update_bot_db_sync_status(self,
                                        session: AsyncSession,
                                        status: str,
                                        details: str,
                                        users_processed: int = 0,
                                        subs_synced: int = 0):
        await panel_sync_dal.update_panel_sync_status(session, status, details,
                                                      users_processed,
                                                      subs_synced)

    async def get_bot_db_last_sync_status(
            self, session: AsyncSession) -> Optional[PanelSyncStatus]:
        return await panel_sync_dal.get_panel_sync_status(session)

    async def get_system_stats(self) -> Optional[Dict[str, Any]]:
        """Get system statistics (CPU, memory, users counts)"""
        response_data = await self._request("GET", "/system/stats", log_full_response=False)
        if response_data and not response_data.get("error") and "response" in response_data:
            return response_data.get("response")
        return None

    async def get_bandwidth_stats(self) -> Optional[Dict[str, Any]]:
        """Get bandwidth statistics"""
        response_data = await self._request("GET", "/system/stats/bandwidth", log_full_response=False)
        if response_data and not response_data.get("error") and "response" in response_data:
            return response_data.get("response")
        return None

    async def get_nodes_statistics(self) -> Optional[Dict[str, Any]]:
        """Get nodes statistics"""
        response_data = await self._request("GET", "/system/stats/nodes", log_full_response=False)
        if response_data and not response_data.get("error") and "response" in response_data:
            return response_data.get("response")
        return None

    async def encrypt_happ_link(self, link_to_encrypt: str) -> Optional[str]:
        """Encrypt a subscription link using the panel's happ crypt4 API.

        Returns the encrypted link string or None if encryption failed.
        """
        payload = {"linkToEncrypt": link_to_encrypt}
        response_data = await self._request(
            "POST",
            "/system/tools/happ/encrypt",
            json=payload,
            log_full_response=False
        )
        if response_data and not response_data.get("error") and "response" in response_data:
            return response_data.get("response", {}).get("encryptedLink")
        logging.error(f"Failed to encrypt happ link. Response: {response_data}")
        return None
